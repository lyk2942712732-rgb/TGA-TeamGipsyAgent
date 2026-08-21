"""Discover models while hiding provider-specific URL layouts from users."""

from __future__ import annotations

from collections.abc import Iterable

import httpx

from .config import ProviderConfig
from .provider_profiles import discovery_urls, provider_profile


def _headers(provider: ProviderConfig) -> dict[str, str]:
    profile = provider_profile(provider.preset_id)
    auth_style = profile.discovery_auth if profile else (
        "anthropic" if set(provider.protocols) == {"anthropic"} else "bearer"
    )
    key = provider.key()
    if auth_style == "anthropic":
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
        raise ValueError("供应商返回了无效的模型列表")
    for row in rows:
        if isinstance(row, str):
            value = row
        elif isinstance(row, dict):
            value = row.get("id") or row.get("name")
        else:
            value = None
        if isinstance(value, str) and value.strip():
            # Native Gemini responses use names such as models/gemini-2.5-pro.
            yield value.strip().removeprefix("models/")


async def _discover_at(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    url: str,
) -> list[str]:
    profile = provider_profile(provider.preset_id)
    paginated = bool(profile and profile.anthropic_pagination)
    found: list[str] = []
    seen: set[str] = set()
    after_id: str | None = None
    for _page in range(20):
        params = {"limit": "100"} if paginated else None
        if params is not None and after_id:
            params["after_id"] = after_id
        response = await client.get(url, headers=_headers(provider), params=params)
        response.raise_for_status()
        payload = response.json()
        for model_id in _model_ids(payload):
            if model_id not in seen:
                seen.add(model_id)
                found.append(model_id)
        if not paginated or not isinstance(payload, dict) or not payload.get("has_more"):
            break
        next_id = payload.get("last_id")
        if not isinstance(next_id, str) or not next_id or next_id == after_id:
            break
        after_id = next_id
    return found


async def discover_provider_models(
    provider: ProviderConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    """Return unique model identifiers using a preset or safe same-origin probing."""

    if not provider.base_url:
        raise ValueError(f"供应商 {provider.id} 尚未配置 API 根地址")
    urls = discovery_urls(provider.preset_id, provider.base_url, provider.protocols)
    failures: list[str] = []
    async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(20.0)) as client:
        for url in urls:
            try:
                found = await _discover_at(client, provider, url)
                if found:
                    return found
                failures.append(f"{url}: 未返回模型")
            except httpx.HTTPStatusError as exc:
                failures.append(f"{url}: HTTP {exc.response.status_code}")
            except httpx.HTTPError as exc:
                failures.append(f"{url}: {exc}")
            except ValueError as exc:
                failures.append(f"{url}: {exc}")
    detail = "；".join(failures)
    raise ValueError(f"读取模型列表失败：{detail}")


__all__ = ["discover_provider_models"]
