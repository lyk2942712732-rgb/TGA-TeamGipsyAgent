"""Local, write-only-at-the-API model provider settings."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


_SETTINGS_LOCK = threading.RLock()
_UNSET = object()
_REASONING_MODES = {"auto", "enabled", "disabled"}
DEFAULT_MAX_OUTPUT_TOKENS = 1024
DEFAULT_REASONING_MAX_OUTPUT_TOKENS = 4096
MIN_MAX_OUTPUT_TOKENS = 256
MAX_MAX_OUTPUT_TOKENS = 16_384
DEFAULT_TIMEOUT_SECONDS = 60
MIN_TIMEOUT_SECONDS = 5
MAX_TIMEOUT_SECONDS = 300
DEFAULT_TEMPERATURE = 0.2


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def model_settings_path() -> Path:
    configured = os.environ.get("TGA_LLM_CONFIG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".tga" / "llm-settings.json").resolve()


def load_model_settings() -> dict[str, Any]:
    path = model_settings_path()
    with _SETTINGS_LOCK:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("local model settings are invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("local model settings must be a JSON object")
        encrypted = payload.pop("api_key_dpapi", None)
        if isinstance(encrypted, str) and encrypted:
            payload["api_key"] = _unprotect_secret(encrypted)
        return payload


def save_model_settings(
    *,
    base_url: str,
    model: str,
    api_key: str | None,
    supports_vision: bool | None,
    max_output_tokens: int | None | object = _UNSET,
    timeout_seconds: int | None | object = _UNSET,
    temperature: float | None | object = _UNSET,
    reasoning_mode: Literal["auto", "enabled", "disabled"] | str | object = _UNSET,
) -> None:
    """Atomically persist provider settings and stale prior verification.

    A missing API key preserves the existing browser-managed key. Environment
    credentials remain available as a fallback without being copied to disk.
    Optional advanced values preserve their existing value when omitted so old
    API clients remain compatible with schema version 2.
    """

    path = model_settings_path()
    with _SETTINGS_LOCK:
        existing = load_model_settings()
        existing_key = existing.get("api_key") if isinstance(existing.get("api_key"), str) else ""
        key = (api_key or "").strip() or existing_key.strip()
        clean_reasoning = _setting_value(reasoning_mode, existing, "reasoning_mode", "auto")
        clean_reasoning = _validate_reasoning_mode(clean_reasoning)
        default_tokens = (
            DEFAULT_REASONING_MAX_OUTPUT_TOKENS if clean_reasoning == "enabled" else DEFAULT_MAX_OUTPUT_TOKENS
        )
        payload: dict[str, Any] = {
            "schema_version": 2,
            "base_url": base_url.strip().rstrip("/"),
            "model": model.strip(),
            "supports_vision": supports_vision,
            "max_output_tokens": _validate_int_setting(
                "max_output_tokens",
                _setting_value(max_output_tokens, existing, "max_output_tokens", default_tokens),
                MIN_MAX_OUTPUT_TOKENS,
                MAX_MAX_OUTPUT_TOKENS,
            ),
            "timeout_seconds": _validate_int_setting(
                "timeout_seconds",
                _setting_value(timeout_seconds, existing, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
                MIN_TIMEOUT_SECONDS,
                MAX_TIMEOUT_SECONDS,
            ),
            "temperature": _validate_temperature(
                _setting_value(temperature, existing, "temperature", DEFAULT_TEMPERATURE)
            ),
            "reasoning_mode": clean_reasoning,
        }

        verification = _safe_verification(existing.get("verification")) or _default_verification()
        config_changed = _safe_configuration(existing) != _safe_configuration(payload)
        key_changed = bool((api_key or "").strip()) and key != existing_key.strip()
        payload["verification"] = dict(verification)
        if verification["status"] in {"verified", "failed"} and (config_changed or key_changed):
            payload["verification"]["status"] = "stale"

        if key:
            if sys.platform == "win32":
                payload["api_key_dpapi"] = _protect_secret(key)
            else:
                payload["api_key"] = key
        _write_settings(payload, path)


def record_model_verification(
    *,
    tool_calling: bool,
    vision: bool | None = None,
    capabilities: dict[str, bool | None] | None = None,
    verification_id: str | None = None,
    verified_at: str | None = None,
) -> dict[str, Any]:
    """Persist a successful capability check without raw responses or secrets."""

    path = model_settings_path()
    with _SETTINGS_LOCK:
        settings = load_model_settings()
        if not settings:
            raise ValueError("model settings are not configured")
        if not tool_calling:
            raise ValueError("tool calling capability must pass before provider verification")
        capability_record = _safe_capabilities(capabilities)
        capability_record["tool_calling"] = bool(tool_calling)
        capability_record["vision"] = vision if isinstance(vision, bool) else None
        fingerprint_input = {
            "configuration": _safe_configuration(settings),
            "capabilities": capability_record,
        }
        verification = {
            "status": "verified",
            "id": verification_id or f"verify_{uuid4().hex}",
            "verified_at": verified_at or datetime.now(timezone.utc).isoformat(),
            "capability_fingerprint": _fingerprint(fingerprint_input),
            "capabilities": capability_record,
            "last_error": None,
        }
        payload = _disk_payload(settings)
        payload["schema_version"] = 2
        payload["verification"] = verification
        _write_settings(payload, path)
        return dict(verification)


def record_model_verification_started() -> dict[str, Any]:
    """Persist the short-lived verification phase without exposing credentials."""

    path = model_settings_path()
    with _SETTINGS_LOCK:
        settings = load_model_settings()
        if not settings:
            raise ValueError("model settings are not configured")
        previous = _safe_verification(settings.get("verification"))
        verification = {
            **previous,
            "status": "verifying",
            "last_error": None,
        }
        payload = _disk_payload(settings)
        payload["schema_version"] = 2
        payload["verification"] = verification
        _write_settings(payload, path)
        return dict(verification)


def record_model_verification_failure(*, code: str, message: str) -> dict[str, Any]:
    """Persist a bounded, redacted verification error and no provider body."""

    path = model_settings_path()
    with _SETTINGS_LOCK:
        settings = load_model_settings()
        if not settings:
            raise ValueError("model settings are not configured")
        previous = _safe_verification(settings.get("verification"))
        verification = {
            "status": "failed",
            "id": previous.get("id"),
            "verified_at": previous.get("verified_at"),
            "capability_fingerprint": previous.get("capability_fingerprint"),
            "capabilities": _safe_capabilities(previous.get("capabilities")),
            "last_error": {
                "code": _safe_text(code, 80),
                "message": _redact(_safe_text(message, 240)),
            },
        }
        payload = _disk_payload(settings)
        payload["schema_version"] = 2
        payload["verification"] = verification
        _write_settings(payload, path)
        return dict(verification)


def effective_model_settings() -> dict[str, Any]:
    """Merge deployment environment overrides with browser settings."""

    local = load_model_settings()

    def text(name: str, env_name: str) -> str:
        environment = os.environ.get(env_name, "").strip()
        if environment:
            return environment
        value = local.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return ""

    reasoning_mode = _environment_reasoning_mode(local)
    default_tokens = (
        DEFAULT_REASONING_MAX_OUTPUT_TOKENS if reasoning_mode == "enabled" else DEFAULT_MAX_OUTPUT_TOKENS
    )
    max_output_tokens = _environment_number(
        "TGA_LLM_MAX_OUTPUT_TOKENS", local.get("max_output_tokens"), default_tokens,
        minimum=MIN_MAX_OUTPUT_TOKENS, maximum=MAX_MAX_OUTPUT_TOKENS, integer=True,
    )
    timeout_seconds = _environment_number(
        "TGA_LLM_TIMEOUT_S", local.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS,
        minimum=MIN_TIMEOUT_SECONDS, maximum=MAX_TIMEOUT_SECONDS, integer=True,
    )
    temperature = _environment_number(
        "TGA_LLM_TEMPERATURE", local.get("temperature"), DEFAULT_TEMPERATURE,
        minimum=0.0, maximum=2.0, integer=False,
    )
    raw_vision = os.environ.get("TGA_LLM_SUPPORTS_VISION", "").strip().casefold()
    if raw_vision:
        supports_vision: bool | None = raw_vision in {"1", "true", "yes", "on"}
    elif "supports_vision" in local and isinstance(local["supports_vision"], bool):
        supports_vision = local["supports_vision"]
    else:
        current_verification = _effective_verification(local)
        capabilities = current_verification.get("capabilities") or {} if current_verification["status"] == "verified" else {}
        verified_vision = capabilities.get("vision") if isinstance(capabilities, dict) else None
        supports_vision = verified_vision if isinstance(verified_vision, bool) else None

    verification = _effective_verification(local)
    return {
        "base_url": text("base_url", "TGA_LLM_BASE_URL").rstrip("/"),
        "model": text("model", "TGA_LLM_MODEL"),
        "api_key": text("api_key", "TGA_LLM_API_KEY"),
        "supports_vision": supports_vision,
        "max_output_tokens": int(max_output_tokens),
        "timeout_seconds": int(timeout_seconds),
        "temperature": float(temperature),
        "reasoning_mode": reasoning_mode,
        "verification": verification,
        "verification_status": verification["status"],
        "browser_configured": bool(local),
    }


def _effective_verification(local: dict[str, Any]) -> dict[str, Any]:
    verification = _safe_verification(local.get("verification"))
    if not verification:
        return _default_verification()
    if verification.get("status") == "verified":
        # Environment overrides alter the effective provider configuration but
        # must never be copied into the browser-managed verification record.
        reasoning_mode = _environment_reasoning_mode(local)
        default_tokens = (
            DEFAULT_REASONING_MAX_OUTPUT_TOKENS if reasoning_mode == "enabled" else DEFAULT_MAX_OUTPUT_TOKENS
        )
        raw_vision = os.environ.get("TGA_LLM_SUPPORTS_VISION", "").strip().casefold()
        effective_safe = {
            "base_url": os.environ.get("TGA_LLM_BASE_URL", "").strip().rstrip("/") or local.get("base_url", ""),
            "model": os.environ.get("TGA_LLM_MODEL", "").strip() or local.get("model", ""),
            "supports_vision": (
                raw_vision in {"1", "true", "yes", "on"}
                if raw_vision else local.get("supports_vision")
            ),
            "max_output_tokens": _environment_number(
                "TGA_LLM_MAX_OUTPUT_TOKENS", local.get("max_output_tokens"), default_tokens,
                minimum=MIN_MAX_OUTPUT_TOKENS, maximum=MAX_MAX_OUTPUT_TOKENS, integer=True,
            ),
            "timeout_seconds": _environment_number(
                "TGA_LLM_TIMEOUT_S", local.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS,
                minimum=MIN_TIMEOUT_SECONDS, maximum=MAX_TIMEOUT_SECONDS, integer=True,
            ),
            "temperature": _environment_number(
                "TGA_LLM_TEMPERATURE", local.get("temperature"), DEFAULT_TEMPERATURE,
                minimum=0.0, maximum=2.0, integer=False,
            ),
            "reasoning_mode": reasoning_mode,
        }
        expected_fingerprint = _fingerprint({
            "configuration": _safe_configuration(local),
            "capabilities": verification["capabilities"],
        })
        if (
            verification.get("capability_fingerprint") != expected_fingerprint
            or effective_safe != _safe_configuration(local)
            or os.environ.get("TGA_LLM_API_KEY", "").strip()
        ):
            verification["status"] = "stale"
    return verification


def _default_verification() -> dict[str, Any]:
    return {
        "status": "unverified", "id": None, "verified_at": None,
        "capability_fingerprint": None, "capabilities": _safe_capabilities(None),
        "last_error": None,
    }


def _safe_verification(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    status = value.get("status")
    if status not in {"unverified", "verifying", "verified", "failed", "stale"}:
        status = "unverified"
    capabilities = _safe_capabilities(value.get("capabilities"))
    last_error = value.get("last_error") if isinstance(value.get("last_error"), dict) else None
    return {
        "status": status,
        "id": _safe_optional_text(value.get("id"), 100),
        "verified_at": _safe_optional_text(value.get("verified_at"), 80),
        "capability_fingerprint": _safe_optional_text(value.get("capability_fingerprint"), 64),
        "capabilities": capabilities,
        "last_error": ({
            "code": _safe_text(last_error.get("code"), 80),
            "message": _redact(_safe_text(last_error.get("message"), 240)),
        } if last_error else None),
    }


def _safe_capabilities(value: Any) -> dict[str, bool | None]:
    source = value if isinstance(value, dict) else {}
    values: dict[str, bool | None] = {
        "chat_completions": False,
        "tool_calling": source.get("tool_calling") is True,
        "forced_tool_choice": False,
        "auto_tool_choice": False,
        "vision": source.get("vision") if isinstance(source.get("vision"), bool) else None,
        "reasoning_content": False,
    }
    for key in ("chat_completions", "forced_tool_choice", "auto_tool_choice", "reasoning_content"):
        values[key] = source.get(key) is True
    return values


def _safe_configuration(value: dict[str, Any]) -> dict[str, Any]:
    reasoning_mode = value.get("reasoning_mode") if value.get("reasoning_mode") in _REASONING_MODES else "auto"
    default_tokens = DEFAULT_REASONING_MAX_OUTPUT_TOKENS if reasoning_mode == "enabled" else DEFAULT_MAX_OUTPUT_TOKENS
    return {
        "base_url": str(value.get("base_url") or "").strip().rstrip("/"),
        "model": str(value.get("model") or "").strip(),
        "supports_vision": value.get("supports_vision") if isinstance(value.get("supports_vision"), bool) else None,
        "max_output_tokens": _coerce_bounded(value.get("max_output_tokens"), default_tokens, MIN_MAX_OUTPUT_TOKENS, MAX_MAX_OUTPUT_TOKENS, True),
        "timeout_seconds": _coerce_bounded(value.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS, True),
        "temperature": _coerce_bounded(value.get("temperature"), DEFAULT_TEMPERATURE, 0.0, 2.0, False),
        "reasoning_mode": reasoning_mode,
    }


def _disk_payload(settings: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in settings.items() if key not in {"api_key", "verification"}}
    key = settings.get("api_key") if isinstance(settings.get("api_key"), str) else ""
    if key:
        if sys.platform == "win32":
            payload["api_key_dpapi"] = _protect_secret(key)
        else:
            payload["api_key"] = key
    return payload


def _write_settings(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _restrict_permissions(temporary, 0o600)
        os.replace(temporary, path)
        _restrict_permissions(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _setting_value(value: Any, existing: dict[str, Any], name: str, default: Any) -> Any:
    if value is _UNSET or value is None:
        return existing.get(name, default)
    return value


def _validate_reasoning_mode(value: Any) -> str:
    clean = str(value).strip().casefold()
    if clean not in _REASONING_MODES:
        raise ValueError("reasoning_mode must be auto, enabled, or disabled")
    return clean


def _validate_int_setting(name: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        clean = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if clean < minimum or clean > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return clean


def _validate_temperature(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("temperature must be a number")
    try:
        clean = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("temperature must be a number") from exc
    if clean < 0 or clean > 2:
        raise ValueError("temperature must be between 0 and 2")
    return clean


def _environment_reasoning_mode(local: dict[str, Any]) -> str:
    value = os.environ.get("TGA_LLM_REASONING_MODE", "").strip() or str(local.get("reasoning_mode") or "auto")
    try:
        return _validate_reasoning_mode(value)
    except ValueError:
        return "auto"


def _environment_number(
    env_name: str, local_value: Any, default: int | float, *, minimum: int | float,
    maximum: int | float, integer: bool,
) -> int | float:
    raw = os.environ.get(env_name, "").strip()
    value = raw if raw else local_value
    return _coerce_bounded(value, default, minimum, maximum, integer)


def _coerce_bounded(
    value: Any, default: int | float, minimum: int | float, maximum: int | float, integer: bool,
) -> int | float:
    try:
        clean = int(value) if integer else float(value)
    except (TypeError, ValueError):
        clean = default
    clean = max(minimum, min(clean, maximum))
    return int(clean) if integer else float(clean)


def _fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _safe_optional_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


def _safe_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _redact(value: str) -> str:
    value = re.sub(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1[REDACTED]", value)
    return re.sub(r"(?i)\b(token|secret|api[_-]?key|password)\s*[=:]\s*[^\s,;}&]+", r"\1=[REDACTED]", value)


def _restrict_permissions(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def _protect_secret(value: str) -> str:
    if sys.platform != "win32":
        return value
    raw = value.encode("utf-8")
    source_buffer = ctypes.create_string_buffer(raw)
    source = _DataBlob(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    protected = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "TGA model API key", None, None, None, 0x1, ctypes.byref(protected),
    ):
        raise OSError(ctypes.get_last_error(), "unable to protect model API key")
    try:
        return base64.b64encode(ctypes.string_at(protected.pbData, protected.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(protected.pbData)


def _unprotect_secret(value: str) -> str:
    if sys.platform != "win32":
        return value
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError("local model credential is invalid") from exc
    source_buffer = ctypes.create_string_buffer(raw)
    source = _DataBlob(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    unprotected = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(unprotected),
    ):
        raise ValueError("local model credential cannot be decrypted by this user")
    try:
        return ctypes.string_at(unprotected.pbData, unprotected.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(unprotected.pbData)
