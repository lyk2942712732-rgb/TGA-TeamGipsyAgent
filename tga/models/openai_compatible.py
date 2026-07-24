"""OpenAI-compatible chat-completions client.

Many domestic model gateways expose this API shape, including private AI safety
gateways used in competitions. This module keeps the dependency surface small by
using the Python standard library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tga.models.base import ModelMessage, ModelResponse
from tga.models.settings import MAX_MAX_OUTPUT_TOKENS, MIN_MAX_OUTPUT_TOKENS


def chat_completions_url(base_url: str) -> str:
    """Return the final endpoint while accepting either an origin or full path."""
    normalized = base_url.strip().rstrip("/")
    if normalized.casefold().endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


@dataclass
class OpenAICompatibleClient:
    base_url: str
    api_key: str
    model: str
    timeout_s: int = 60
    max_tokens: int = 512
    temperature: float = 0.2
    provider_name: str = "openai-compatible"
    supports_vision: bool | None = None
    reasoning_mode: str = "auto"

    @property
    def chat_completions_url(self) -> str:
        return chat_completions_url(self.base_url)

    def chat_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Run one native agent turn and preserve the provider tool envelope.

        BreachWeave keeps assistant ``tool_calls`` and matching ``tool``
        messages in one AgentSession.  Returning the raw assistant message is
        required for the same protocol; flattening it into a JSON planning
        string was the source of the old one-action-at-a-time runtime.
        """
        url = self.chat_completions_url
        output_budget = self.max_tokens if max_tokens is None else max_tokens
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": output_budget,
        }
        self._apply_reasoning_mode(payload)
        try:
            raw = self._post_json(url, payload)
        except RuntimeError:
            raise
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(self._provider_error("agent request", exc)) from exc
        choice = raw.get("choices", [{}])[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("model agent response did not contain an assistant message")
        retry_metadata: dict[str, Any] | None = None
        if self._reasoning_tool_call_was_truncated(choice, message):
            retry_budget = min(MAX_MAX_OUTPUT_TOKENS, max(output_budget * 2, output_budget + 512))
            if retry_budget > output_budget:
                retry_payload = dict(payload)
                retry_payload["max_tokens"] = retry_budget
                try:
                    raw = self._post_json(url, retry_payload)
                except RuntimeError:
                    raise
                except (HTTPError, URLError, TimeoutError) as exc:
                    raise RuntimeError(self._provider_error("agent retry", exc)) from exc
                choice = raw.get("choices", [{}])[0]
                message = choice.get("message")
                if not isinstance(message, dict):
                    raise RuntimeError("model agent retry did not contain an assistant message")
                retry_metadata = {
                    "event": "PROVIDER_RETRY",
                    "reason": "tool_call_truncated_after_reasoning",
                    "attempts": 2,
                    "previous_max_output_tokens": output_budget,
                    "retry_max_output_tokens": retry_budget,
                }
        result = {
            "message": message,
            "finish_reason": choice.get("finish_reason"),
            "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else {},
            "request_id": raw.get("id"),
        }
        if retry_metadata is not None:
            result["provider_retry"] = retry_metadata
        return result

    def chat(self, messages: list[ModelMessage], *, temperature: float | None = None) -> ModelResponse:
        url = self.chat_completions_url
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens,
        }
        self._apply_reasoning_mode(payload)
        try:
            raw = self._post_json(url, payload)
        except RuntimeError:
            raise
        content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
        return ModelResponse(content=content, model=self.model, raw=raw)

    def chat_action_tool(
        self,
        messages: list[ModelMessage],
        *,
        tool_name: str,
        tool_description: str,
        parameters: dict,
        thinking: bool | None = None,
        temperature: float | None = None,
    ) -> ModelResponse:
        """Request one native OpenAI-compatible function call.

        OpenAI-compatible providers validate a tool-call envelope more
        reliably than an instruction asking the
        model to print JSON.  The runtime still validates the arguments and
        executes nothing from this client directly.
        """
        url = self.chat_completions_url
        payload = {
            "model": self.model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens,
            "tools": [{"type": "function", "function": {
                "name": tool_name, "description": tool_description, "parameters": parameters,
            }}],
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
        }
        if thinking is not None:
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        else:
            self._apply_reasoning_mode(payload)
        try:
            raw = self._post_json(url, payload)
        except RuntimeError as exc:
            safe_error = str(exc)
            # Some reasoning models accept tools but reject a forced
            # ``tool_choice``. Retry with the same bounded tool catalog in
            # automatic mode;
            # the host still validates every returned argument before use.
            if "status=400" in safe_error and thinking is not False and "does not support this tool_choice" in safe_error.casefold():
                fallback_payload = dict(payload)
                fallback_payload.pop("tool_choice", None)
                try:
                    raw = self._post_json(url, fallback_payload)
                except RuntimeError as retry_exc:
                    raise RuntimeError(f"provider tool retry failed: {retry_exc}") from retry_exc
            else:
                raise
        choice = raw.get("choices", [{}])[0]
        message = choice.get("message", {})
        calls = message.get("tool_calls") or []
        selected = next(
            (
                item for item in calls
                if isinstance(item, dict) and (item.get("function") or {}).get("name") == tool_name
            ),
            None,
        )
        if selected is None:
            finish_reason = str(choice.get("finish_reason") or "unknown")[:80]
            message_keys = ",".join(sorted(str(key) for key in message))[:160]
            raise RuntimeError(
                f"model did not return required tool {tool_name}; finish_reason={finish_reason}; message_fields={message_keys or 'none'}"
            )
        arguments = (selected.get("function") or {}).get("arguments")
        if not isinstance(arguments, str) or not arguments.strip():
            raise RuntimeError("model action tool call has no arguments")
        return ModelResponse(content=arguments, model=self.model, raw=raw)

    def _post_json(self, url: str, payload: dict) -> dict:
        # Some Windows/browser inputs can contain an unpaired UTF-16
        # surrogate. Python's default JSON encoder serializes it as a lone
        # ``\\udxxx`` escape, which strict OpenAI-compatible gateways reject.
        # Normalize every string at the provider boundary so one malformed UI
        # character cannot kill a Solver before its first tool call.
        payload = _unicode_scalar_value(payload)
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(self._provider_error("request", exc)) from exc
        if not isinstance(raw, dict):
            raise RuntimeError("provider request returned an invalid JSON envelope")
        return raw

    def preflight_tools(self, tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Perform one minimal tool-capability request before a task starts."""
        if not tools:
            raise RuntimeError("provider tool preflight requires at least one tool definition")
        result = self.chat_tools(
            [{"role": "system", "content": "Return a tool call if the tool protocol is supported."},
             {"role": "user", "content": "Protocol preflight."}],
            tools=tools[:1], temperature=0, max_tokens=max(MIN_MAX_OUTPUT_TOKENS, self.max_tokens),
        )
        message = result.get("message")
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        if not isinstance(calls, list) or not calls:
            raise RuntimeError("provider tool preflight failed: model returned no function call")
        return {"ok": True, "request_id": result.get("request_id")}

    def _provider_error(self, phase: str, exc: BaseException, *, body: str | None = None) -> str:
        if isinstance(exc, HTTPError):
            request_id = exc.headers.get("x-request-id") or exc.headers.get("request-id") or "unknown"
            try:
                payload = json.loads(body if body is not None else exc.read().decode("utf-8", errors="replace"))
                message = payload.get("error", {}).get("message", "provider request failed") if isinstance(payload, dict) else "provider request failed"
            except Exception:
                message = "provider request failed"
            return f"provider {phase} failed: status={exc.code} type=http_error request_id={request_id} message={_redact(str(message))[:240]}"
        return f"provider {phase} failed: type={type(exc).__name__} message={_redact(str(exc))[:240]}"

    def _apply_reasoning_mode(self, payload: dict[str, Any]) -> None:
        if self.reasoning_mode in {"enabled", "disabled"}:
            payload["thinking"] = {"type": self.reasoning_mode}

    @staticmethod
    def _reasoning_tool_call_was_truncated(choice: dict[str, Any], message: dict[str, Any]) -> bool:
        reasoning = message.get("reasoning_content")
        return (
            choice.get("finish_reason") == "length"
            and not message.get("tool_calls")
            and isinstance(reasoning, str)
            and bool(reasoning.strip())
        )

    def chat_stream(self, messages: list[ModelMessage], *, temperature: float | None = None) -> Iterable[str]:
        url = self.chat_completions_url
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        self._apply_reasoning_mode(payload)
        body = json.dumps(_unicode_scalar_value(payload), ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    delta = payload.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        yield str(delta)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(self._provider_error("stream request", exc)) from exc


def _unicode_scalar_value(value: Any) -> Any:
    """Recursively replace unpaired surrogates with U+FFFD."""
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(value, list):
        return [_unicode_scalar_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_unicode_scalar_value(item) for item in value)
    if isinstance(value, dict):
        return {
            _unicode_scalar_value(key) if isinstance(key, str) else key: _unicode_scalar_value(item)
            for key, item in value.items()
        }
    return value


def _redact(value: str) -> str:
    import re
    value = re.sub(r"(?i)(authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*:\s*[^\r\n]+", r"\1: [REDACTED]", value)
    value = re.sub(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)\b(token|secret|api[_-]?key|password)\s*[=:]\s*[^\s,;}&]+", r"\1=[REDACTED]", value)
    return value
