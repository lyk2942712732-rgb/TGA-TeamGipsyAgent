"""OpenAI-compatible chat-completions client.

Many domestic model gateways expose this API shape, including private AI safety
gateways used in competitions. This module keeps the dependency surface small by
using the Python standard library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tga.models.base import ModelMessage, ModelResponse


@dataclass
class OpenAICompatibleClient:
    base_url: str
    api_key: str
    model: str
    timeout_s: int = 60

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
