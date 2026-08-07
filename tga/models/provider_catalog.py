"""Persistent multi-provider model catalog with write-only credentials."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tga.models.openai_compatible import OpenAICompatibleClient
from tga.models.settings import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_SECONDS,
    _protect_secret,
    _redact,
    _unprotect_secret,
    _write_settings,
    load_model_settings,
    model_settings_path,
)


PROVIDER_PRESETS: tuple[dict[str, str], ...] = (
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1"},
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com"},
    {"id": "moonshot", "name": "Moonshot AI", "base_url": "https://api.moonshot.cn/v1"},
    {"id": "siliconflow", "name": "SiliconFlow", "base_url": "https://api.siliconflow.cn/v1"},
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
    {"id": "groq", "name": "Groq", "base_url": "https://api.groq.com/openai/v1"},
)

_LOCK = threading.RLock()


def provider_catalog_path() -> Path:
    configured = os.environ.get("TGA_LLM_PROVIDERS_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return model_settings_path().with_name("llm-providers.json")


def list_provider_catalog() -> dict[str, Any]:
    with _LOCK:
        payload = _load_catalog(migrate=True)
        return {
            "schema_version": 1,
            "presets": [dict(item) for item in PROVIDER_PRESETS],
            "providers": [_public_provider(item) for item in payload["providers"]],
        }


def create_provider(
    *, name: str, base_url: str, preset_id: str | None, model: str,
    api_key: str, api_key_label: str | None = None,
) -> dict[str, Any]:
    now = _now()
    provider_id = f"provider_{uuid4().hex[:16]}"
    model_id = f"model_{uuid4().hex[:16]}"
    key_id = f"key_{uuid4().hex[:16]}"
    provider = {
        "id": provider_id,
        "name": name.strip(),
        "preset_id": (preset_id or "custom").strip(),
        "base_url": base_url.strip().rstrip("/"),
        "models": [_new_model(model_id, model, now)],
        "api_keys": [_new_key(key_id, api_key, api_key_label, now)],
        "selected_api_key_id": key_id,
        "created_at": now,
        "updated_at": now,
    }
    with _LOCK:
        payload = _load_catalog(migrate=True)
        payload["providers"].append(provider)
        _save_catalog(payload)
    return _public_provider(provider)


def add_provider_model(
    *, provider_id: str, name: str, supports_vision: bool | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    temperature: float = DEFAULT_TEMPERATURE,
    reasoning_mode: str = "auto",
) -> dict[str, Any]:
    with _LOCK:
        payload = _load_catalog(migrate=True)
        provider = _provider(payload, provider_id)
        if any(item["name"].casefold() == name.strip().casefold() for item in provider["models"]):
            raise ValueError("model name already exists for this provider")
        model = _new_model(
            f"model_{uuid4().hex[:16]}", name, _now(), supports_vision=supports_vision,
            max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds,
            temperature=temperature, reasoning_mode=reasoning_mode,
        )
        provider["models"].append(model)
        provider["updated_at"] = _now()
        _save_catalog(payload)
        return _public_model(model)


def add_provider_api_key(
    *, provider_id: str, api_key: str, label: str | None = None,
) -> dict[str, Any]:
    with _LOCK:
        payload = _load_catalog(migrate=True)
        provider = _provider(payload, provider_id)
        key = _new_key(f"key_{uuid4().hex[:16]}", api_key, label, _now())
        provider["api_keys"].append(key)
        provider["selected_api_key_id"] = key["id"]
        _stale_models(provider)
        provider["updated_at"] = _now()
        _save_catalog(payload)
        return _public_key(key, selected=True)


def select_provider_api_key(*, provider_id: str, api_key_id: str) -> dict[str, Any]:
    with _LOCK:
        payload = _load_catalog(migrate=True)
        provider = _provider(payload, provider_id)
        if not any(item["id"] == api_key_id for item in provider["api_keys"]):
            raise KeyError(api_key_id)
        if provider.get("selected_api_key_id") != api_key_id:
            provider["selected_api_key_id"] = api_key_id
            _stale_models(provider)
            provider["updated_at"] = _now()
            _save_catalog(payload)
        return _public_provider(provider)


def model_target_status(
    *, provider_id: str, model_id: str, api_key_id: str | None = None,
) -> dict[str, Any]:
    with _LOCK:
        payload = _load_catalog(migrate=True)
        provider = _provider(payload, provider_id)
        model = _model(provider, model_id)
        key_id = api_key_id or str(provider.get("selected_api_key_id") or "")
        key = _key(provider, key_id)
        verification = _verification(model.get("verification"))
        current_fingerprint = _configuration_fingerprint(provider, model, key_id)
        if verification["status"] == "verified" and verification.get("configuration_fingerprint") != current_fingerprint:
            verification["status"] = "stale"
        return {
            "configured": bool(provider.get("base_url") and model.get("name") and key),
            "provider": provider["name"],
            "provider_id": provider["id"],
            "base_url": provider["base_url"],
            "model": model["name"],
            "model_id": model["id"],
            "api_key_id": key_id,
            "api_key": _secret(key),
            "api_key_set": True,
            "supports_vision": model.get("supports_vision"),
            "max_output_tokens": int(model["max_output_tokens"]),
            "timeout_seconds": int(model["timeout_seconds"]),
            "temperature": float(model["temperature"]),
            "reasoning_mode": model["reasoning_mode"],
            "verification_status": verification["status"],
            "verification": verification,
            "configuration_fingerprint": current_fingerprint,
        }


def build_catalog_model_client(
    *, provider_id: str, model_id: str, api_key_id: str | None = None,
) -> OpenAICompatibleClient:
    status = model_target_status(
        provider_id=provider_id, model_id=model_id, api_key_id=api_key_id,
    )
    return OpenAICompatibleClient(
        base_url=status["base_url"], api_key=status["api_key"], model=status["model"],
        supports_vision=status["supports_vision"], temperature=status["temperature"],
        timeout_s=status["timeout_seconds"], max_tokens=status["max_output_tokens"],
        reasoning_mode=status["reasoning_mode"], provider_name=status["provider"],
    )


def record_catalog_verification(
    *, provider_id: str, model_id: str, capabilities: dict[str, bool | None],
    verification_id: str | None = None,
) -> dict[str, Any]:
    with _LOCK:
        payload = _load_catalog(migrate=True)
        provider = _provider(payload, provider_id)
        model = _model(provider, model_id)
        key_id = str(provider.get("selected_api_key_id") or "")
        verification = {
            "status": "verified",
            "id": verification_id or f"verify_{uuid4().hex}",
            "verified_at": _now(),
            "configuration_fingerprint": _configuration_fingerprint(provider, model, key_id),
            "capability_fingerprint": _capability_fingerprint(provider, model, key_id, capabilities),
            "capabilities": dict(capabilities),
            "last_error": None,
        }
        model["verification"] = verification
        provider["updated_at"] = _now()
        _save_catalog(payload)
        return dict(verification)


def record_catalog_verification_failure(
    *, provider_id: str, model_id: str, code: str, message: str,
) -> dict[str, Any]:
    with _LOCK:
        payload = _load_catalog(migrate=True)
        provider = _provider(payload, provider_id)
        model = _model(provider, model_id)
        verification = _verification(model.get("verification"))
        verification.update({
            "status": "failed",
            "last_error": {"code": str(code)[:80], "message": _redact(str(message)[:240])},
        })
        model["verification"] = verification
        _save_catalog(payload)
        return dict(verification)


def _load_catalog(*, migrate: bool) -> dict[str, Any]:
    path = provider_catalog_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {"schema_version": 1, "providers": []}
    except json.JSONDecodeError as exc:
        raise ValueError("local provider catalog is invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("providers"), list):
        raise ValueError("local provider catalog must contain a providers list")
    if migrate and not payload["providers"]:
        migrated = _legacy_provider()
        if migrated is not None:
            payload["providers"].append(migrated)
            _save_catalog(payload)
    return payload


def _save_catalog(payload: dict[str, Any]) -> None:
    _write_settings({"schema_version": 1, "providers": payload["providers"]}, provider_catalog_path())


def _legacy_provider() -> dict[str, Any] | None:
    legacy = load_model_settings()
    base_url = str(legacy.get("base_url") or "").strip().rstrip("/")
    model_name = str(legacy.get("model") or "").strip()
    secret = str(legacy.get("api_key") or "").strip()
    if not (base_url and model_name and secret):
        return None
    now = _now()
    provider_id, model_id, key_id = "provider_legacy", "model_legacy", "key_legacy"
    model = _new_model(
        model_id, model_name, now, supports_vision=legacy.get("supports_vision"),
        max_output_tokens=int(legacy.get("max_output_tokens") or DEFAULT_MAX_OUTPUT_TOKENS),
        timeout_seconds=int(legacy.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
        temperature=float(legacy.get("temperature") if legacy.get("temperature") is not None else DEFAULT_TEMPERATURE),
        reasoning_mode=str(legacy.get("reasoning_mode") or "auto"),
    )
    old_verification = legacy.get("verification")
    if isinstance(old_verification, dict):
        model["verification"] = dict(old_verification)
    return {
        "id": provider_id, "name": "Imported provider", "preset_id": "custom",
        "base_url": base_url, "models": [model],
        "api_keys": [_new_key(key_id, secret, "Imported key", now)],
        "selected_api_key_id": key_id, "created_at": now, "updated_at": now,
    }


def _new_model(
    model_id: str, name: str, now: str, *, supports_vision: bool | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    temperature: float = DEFAULT_TEMPERATURE,
    reasoning_mode: str = "auto",
) -> dict[str, Any]:
    return {
        "id": model_id, "name": name.strip(), "supports_vision": supports_vision,
        "max_output_tokens": max_output_tokens, "timeout_seconds": timeout_seconds,
        "temperature": temperature, "reasoning_mode": reasoning_mode,
        "verification": _verification(None), "created_at": now,
    }


def _new_key(key_id: str, secret: str, label: str | None, now: str) -> dict[str, Any]:
    clean = secret.strip()
    if not clean:
        raise ValueError("API key must not be blank")
    suffix = clean[-4:] if len(clean) >= 8 else ""
    key: dict[str, Any] = {
        "id": key_id,
        "label": (label or f"Key{f' ••••{suffix}' if suffix else ''}").strip(),
        "created_at": now,
    }
    if os.name == "nt":
        key["secret_dpapi"] = _protect_secret(clean)
    else:
        key["secret"] = clean
    return key


def _secret(key: dict[str, Any]) -> str:
    if isinstance(key.get("secret_dpapi"), str):
        return _unprotect_secret(key["secret_dpapi"])
    return str(key.get("secret") or "")


def _provider(payload: dict[str, Any], provider_id: str) -> dict[str, Any]:
    for provider in payload["providers"]:
        if provider.get("id") == provider_id:
            return provider
    raise KeyError(provider_id)


def _model(provider: dict[str, Any], model_id: str) -> dict[str, Any]:
    for model in provider.get("models", []):
        if model.get("id") == model_id:
            return model
    raise KeyError(model_id)


def _key(provider: dict[str, Any], key_id: str) -> dict[str, Any]:
    for key in provider.get("api_keys", []):
        if key.get("id") == key_id:
            return key
    raise KeyError(key_id)


def _public_provider(provider: dict[str, Any]) -> dict[str, Any]:
    selected = str(provider.get("selected_api_key_id") or "")
    return {
        "id": provider["id"], "name": provider["name"],
        "preset_id": provider.get("preset_id") or "custom",
        "base_url": provider["base_url"],
        "models": [_public_model(item) for item in provider.get("models", [])],
        "api_keys": [_public_key(item, selected=item.get("id") == selected) for item in provider.get("api_keys", [])],
        "selected_api_key_id": selected or None,
        "created_at": provider.get("created_at"), "updated_at": provider.get("updated_at"),
    }


def _public_model(model: dict[str, Any]) -> dict[str, Any]:
    verification = _verification(model.get("verification"))
    return {
        "id": model["id"], "name": model["name"],
        "supports_vision": model.get("supports_vision"),
        "max_output_tokens": model.get("max_output_tokens"),
        "timeout_seconds": model.get("timeout_seconds"),
        "temperature": model.get("temperature"),
        "reasoning_mode": model.get("reasoning_mode"),
        "verification_status": verification["status"],
        "verification": verification,
    }


def _public_key(key: dict[str, Any], *, selected: bool) -> dict[str, Any]:
    secret = _secret(key)
    suffix = secret[-4:] if len(secret) >= 8 else ""
    masked = "•" * min(12, max(8, len(secret) - len(suffix))) + suffix
    return {
        "id": key["id"], "label": key.get("label") or "API key",
        "masked": masked, "selected": selected, "created_at": key.get("created_at"),
    }


def _verification(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    status = source.get("status")
    if status not in {"unverified", "verifying", "verified", "failed", "stale"}:
        status = "unverified"
    return {
        "status": status, "id": source.get("id"), "verified_at": source.get("verified_at"),
        "configuration_fingerprint": source.get("configuration_fingerprint"),
        "capability_fingerprint": source.get("capability_fingerprint"),
        "capabilities": source.get("capabilities") if isinstance(source.get("capabilities"), dict) else {},
        "last_error": source.get("last_error") if isinstance(source.get("last_error"), dict) else None,
    }


def _stale_models(provider: dict[str, Any]) -> None:
    for model in provider.get("models", []):
        verification = _verification(model.get("verification"))
        if verification["status"] == "verified":
            verification["status"] = "stale"
        model["verification"] = verification


def _configuration_payload(provider: dict[str, Any], model: dict[str, Any], key_id: str) -> dict[str, Any]:
    return {
        "provider_id": provider["id"], "base_url": provider["base_url"],
        "model_id": model["id"], "model": model["name"], "api_key_id": key_id,
        "supports_vision": model.get("supports_vision"),
        "max_output_tokens": model["max_output_tokens"], "timeout_seconds": model["timeout_seconds"],
        "temperature": model["temperature"], "reasoning_mode": model["reasoning_mode"],
    }


def _configuration_fingerprint(provider: dict[str, Any], model: dict[str, Any], key_id: str) -> str:
    return _hash(_configuration_payload(provider, model, key_id))


def _capability_fingerprint(
    provider: dict[str, Any], model: dict[str, Any], key_id: str,
    capabilities: dict[str, bool | None],
) -> str:
    return _hash({"configuration": _configuration_payload(provider, model, key_id), "capabilities": capabilities})


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "PROVIDER_PRESETS", "add_provider_api_key", "add_provider_model",
    "build_catalog_model_client", "create_provider", "list_provider_catalog",
    "model_target_status", "provider_catalog_path", "record_catalog_verification",
    "record_catalog_verification_failure", "select_provider_api_key",
]
