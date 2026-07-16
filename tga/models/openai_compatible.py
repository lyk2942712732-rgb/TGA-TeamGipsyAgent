"""OpenAI-compatible chat-completions client.

Many domestic model gateways expose this API shape, including private AI safety
gateways used in competitions. This module keeps the dependency surface small by
using the Python standard library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tga.models.base import ModelMessage, ModelResponse


@dataclass
class OpenAICompatibleClient:
    base_url: str
    api_key: str
    model: str
    timeout_s: int = 60

    def chat_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Run one native agent turn and preserve the provider tool envelope.

        BreachWeave keeps assistant ``tool_calls`` and matching ``tool``
        messages in one AgentSession.  Returning the raw assistant message is
        required for the same protocol; flattening it into a JSON planning
        string was the source of the old one-action-at-a-time runtime.
        """
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "tools": tools,
            "tool_choice": "auto",
        }
        try:
            raw = self._post_json(url, payload)
        except HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"model agent request failed: {exc.code} {text}") from exc
        choice = raw.get("choices", [{}])[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("model agent response did not contain an assistant message")
        return {"message": message, "finish_reason": choice.get("finish_reason"), "raw": raw}

    def chat(self, messages: list[ModelMessage], *, temperature: float = 0.2) -> ModelResponse:
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": message.role, "content": message.content} for message in messages],
                "temperature": temperature,
            }
        ).encode("utf-8")
        request = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"model request failed: {exc.code} {text}") from exc
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
        temperature: float = 0.2,
    ) -> ModelResponse:
        """Request one native OpenAI-compatible function call.

        Modern OpenAI-compatible providers (including DeepSeek V4) validate
        a tool-call envelope more reliably than an instruction asking the
        model to print JSON.  The runtime still validates the arguments and
        executes nothing from this client directly.
        """
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": temperature,
            "tools": [{"type": "function", "function": {
                "name": tool_name, "description": tool_description, "parameters": parameters,
            }}],
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
        }
        if thinking is not None:
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        try:
            raw = self._post_json(url, payload)
        except HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            # DeepSeek reasoning/thinking models accept tools but reject a
            # forced ``tool_choice``.  BreachWeave-style provider negotiation
            # retries with the same bounded tool catalog in automatic mode;
            # the host still validates every returned argument before use.
            if exc.code == 400 and thinking is not False and "does not support this tool_choice" in text.casefold():
                fallback_payload = dict(payload)
                fallback_payload.pop("tool_choice", None)
                try:
                    raw = self._post_json(url, fallback_payload)
                except HTTPError as retry_exc:
                    retry_text = retry_exc.read().decode("utf-8", errors="replace")
                    raise RuntimeError(f"model tool request failed after automatic tool selection: {retry_exc.code} {retry_text}") from retry_exc
            else:
                raise RuntimeError(f"model tool request failed: {exc.code} {text}") from exc
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
        with urlopen(request, timeout=self.timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))

    def chat_stream(self, messages: list[ModelMessage], *, temperature: float = 0.2) -> Iterable[str]:
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": message.role, "content": message.content} for message in messages],
                "temperature": temperature,
                "stream": True,
            }
        ).encode("utf-8")
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
        except HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"model stream failed: {exc.code} {text}") from exc


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
