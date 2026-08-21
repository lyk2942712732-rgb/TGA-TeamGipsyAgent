"""Discover the models exposed by a configured provider API."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse

import httpx

from .config import ProviderConfig


def _models_url(provider: ProviderConfig) -> str:
    raw = (provider.base_url or "").strip().rstrip("/")
    if not raw:
        raise ValueError(f"provider {provider.id} has no API URL")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"provider {provider.id} has an invalid API URL")
    if raw.endswith("/models"):
        return raw
    if provider.protocol == "anthropic" and not raw.endswith("/v1"):
        return f"{raw}/v1/models"
    return f"{raw}/models"


def _headers(provider: ProviderConfig) -> dict[str, str]:
    key = provider.key()
    if provider.protocol == "anthropic":
        return {
            "accept": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": key,
        }
    return {"accept": "application/json", "authorization": f"Bearer {key}"}


def _model_ids(payload: object) -> Iterable[str]:
    rows: object
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data", payload.get("models", []))
    else:
        rows = []
    if not isinstance(rows, list):
        raise ValueError("provider returned an invalid model list")
    for row in rows:
        if isinstance(row, str):
            value = row
        elif isinstance(row, dict):
            value = row.get("id") or row.get("name")
        else:
            value = None
        if isinstance(value, str) and value.strip():
            yield value.strip()


async def discover_provider_models(
    provider: ProviderConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    """Return unique API model identifiers without ever exposing the selected key."""

    url = _models_url(provider)
    headers = _headers(provider)
    found: list[str] = []
    seen: set[str] = set()
    after_id: str | None = None
    async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(20.0)) as client:
        for _page in range(20):
            params = {"limit": "100"} if provider.protocol == "anthropic" else None
            if params is not None and after_id:
                params["after_id"] = after_id
            try:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPStatusError as exc:
                raise ValueError(f"读取模型列表失败：HTTP {exc.response.status_code}") from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise ValueError(f"读取模型列表失败：{exc}") from exc
            for model_id in _model_ids(payload):
                if model_id not in seen:
                    seen.add(model_id)
                    found.append(model_id)
            if provider.protocol != "anthropic" or not isinstance(payload, dict) or not payload.get("has_more"):
                break
            next_id = payload.get("last_id")
            if not isinstance(next_id, str) or not next_id or next_id == after_id:
                break
            after_id = next_id
    if not found:
        raise ValueError("API 未返回任何可访问模型")
    return found


__all__ = ["discover_provider_models"]
