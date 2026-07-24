from __future__ import annotations

import json
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from tga.models.bootstrap import build_model_client, model_config_status
from tga.models.openai_compatible import OpenAICompatibleClient
from tga.models.settings import (
    effective_model_settings,
    model_settings_path,
    record_model_verification,
    record_model_verification_failure,
    record_model_verification_started,
    save_model_settings,
)


def test_browser_model_settings_persist_and_build_client(monkeypatch) -> None:
    for name in ("TGA_LLM_API_KEY", "TGA_LLM_BASE_URL", "TGA_LLM_MODEL", "TGA_LLM_SUPPORTS_VISION"):
        monkeypatch.delenv(name, raising=False)

    save_model_settings(
        base_url="https://provider.example/v1/",
        model="tool-model",
        api_key="browser-secret",
        supports_vision=True,
    )

    client = build_model_client()
    assert client is not None
    assert client.base_url == "https://provider.example/v1"
    assert client.model == "tool-model"
    assert client.api_key == "browser-secret"
    assert client.supports_vision is True
    status = model_config_status()
    assert status["configured"] is True
    assert status["base_url"] == "https://provider.example/v1"
    assert status["model"] == "tool-model"
    assert status["provider"] == "openai-compatible"
    assert status["api_key_set"] is True
    assert status["browser_configured"] is True
    assert status["temperature"] == 0.2
    assert status["max_output_tokens"] == 1024
    assert status["timeout_seconds"] == 60
    assert status["reasoning_mode"] == "auto"
    assert status["supports_vision"] is True
    assert status["verification_status"] == "unverified"
    assert status["chat_completions_url"] == "https://provider.example/v1/chat/completions"


def test_blank_browser_key_preserves_existing_key(monkeypatch) -> None:
    monkeypatch.delenv("TGA_LLM_API_KEY", raising=False)
    save_model_settings(base_url="https://one.example/v1", model="one", api_key="kept-secret", supports_vision=None)
    save_model_settings(base_url="https://two.example/v1", model="two", api_key=None, supports_vision=False)

    payload = json.loads(model_settings_path().read_text(encoding="utf-8"))
    assert "kept-secret" not in model_settings_path().read_text(encoding="utf-8") if sys.platform == "win32" else payload["api_key"] == "kept-secret"
    assert effective_model_settings()["api_key"] == "kept-secret"
    assert effective_model_settings()["model"] == "two"


def test_environment_values_override_browser_settings(monkeypatch) -> None:
    save_model_settings(base_url="https://browser.example/v1", model="browser-model", api_key="browser-key", supports_vision=False)
    monkeypatch.setenv("TGA_LLM_BASE_URL", "https://deployment.example/v1")
    monkeypatch.setenv("TGA_LLM_MODEL", "deployment-model")
    monkeypatch.setenv("TGA_LLM_API_KEY", "deployment-key")
    monkeypatch.setenv("TGA_LLM_SUPPORTS_VISION", "true")

    settings = effective_model_settings()
    assert settings["base_url"] == "https://deployment.example/v1"
    assert settings["model"] == "deployment-model"
    assert settings["api_key"] == "deployment-key"
    assert settings["supports_vision"] is True


def test_browser_settings_reject_credentials_or_query_in_base_url() -> None:
    client = TestClient(app)
    for base_url in ("https://user:secret@provider.example/v1", "https://provider.example/v1?token=secret"):
        response = client.post(
            "/api/v2/settings/llm",
            json={"base_url": base_url, "model": "tool-model", "api_key": "safe-channel-secret"},
        )
        assert response.status_code == 422


def test_advanced_settings_persist_and_build_client(monkeypatch) -> None:
    for name in (
        "TGA_LLM_API_KEY", "TGA_LLM_BASE_URL", "TGA_LLM_MODEL", "TGA_LLM_SUPPORTS_VISION",
        "TGA_LLM_MAX_OUTPUT_TOKENS", "TGA_LLM_TIMEOUT_S", "TGA_LLM_TEMPERATURE",
        "TGA_LLM_REASONING_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    save_model_settings(
        base_url="https://provider.example/v1/chat/completions/",
        model="reasoner",
        api_key="safe-secret",
        supports_vision=None,
        max_output_tokens=4096,
        timeout_seconds=90,
        temperature=0.65,
        reasoning_mode="enabled",
    )

    client = build_model_client()
    assert client is not None
    assert client.max_tokens == 4096
    assert client.timeout_s == 90
    assert client.temperature == 0.65
    assert client.reasoning_mode == "enabled"
    assert client.chat_completions_url == "https://provider.example/v1/chat/completions"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_output_tokens", 255, "max_output_tokens must be between 256 and 16384"),
        ("timeout_seconds", 301, "timeout_seconds must be between 5 and 300"),
        ("temperature", 2.1, "temperature must be between 0 and 2"),
        ("reasoning_mode", "sometimes", "reasoning_mode must be auto, enabled, or disabled"),
    ],
)
def test_advanced_settings_validate_product_bounds(field: str, value: Any, message: str) -> None:
    kwargs: dict[str, Any] = {
        "base_url": "https://provider.example/v1",
        "model": "model",
        "api_key": "secret",
        "supports_vision": None,
        field: value,
    }
    with pytest.raises(ValueError, match=message):
        save_model_settings(**kwargs)


def test_verification_persists_safe_fingerprint_and_configuration_change_stales() -> None:
    save_model_settings(
        base_url="https://provider.example/v1", model="model-a", api_key="top-secret",
        supports_vision=None, max_output_tokens=1024, timeout_seconds=60,
        temperature=0.2, reasoning_mode="auto",
    )
    verification = record_model_verification(
        tool_calling=True, vision=True, verification_id="verify_test", verified_at="2026-07-24T00:00:00+00:00",
    )

    persisted_text = model_settings_path().read_text(encoding="utf-8")
    persisted = json.loads(persisted_text)
    assert verification["status"] == "verified"
    assert len(verification["capability_fingerprint"]) == 64
    assert persisted["verification"] == verification
    assert "top-secret" not in persisted["verification"].__repr__()
    assert "choices" not in persisted_text
    assert effective_model_settings()["supports_vision"] is True

    save_model_settings(
        base_url="https://provider.example/v1", model="model-b", api_key=None, supports_vision=None,
    )
    stale = json.loads(model_settings_path().read_text(encoding="utf-8"))["verification"]
    assert stale["status"] == "stale"
    assert stale["capability_fingerprint"] == verification["capability_fingerprint"]
    assert effective_model_settings()["supports_vision"] is None


def test_replacing_api_key_stales_verification_without_key_fingerprint() -> None:
    save_model_settings(
        base_url="https://provider.example/v1", model="model", api_key="first-secret", supports_vision=False,
    )
    record_model_verification(tool_calling=True)
    save_model_settings(
        base_url="https://provider.example/v1", model="model", api_key="second-secret", supports_vision=False,
    )

    persisted = json.loads(model_settings_path().read_text(encoding="utf-8"))
    verification_text = json.dumps(persisted["verification"])
    assert persisted["verification"]["status"] == "stale"
    assert "first-secret" not in verification_text
    assert "second-secret" not in verification_text


def test_verification_fingerprint_detects_out_of_band_configuration_change() -> None:
    save_model_settings(
        base_url="https://provider.example/v1", model="model", api_key="secret", supports_vision=None,
    )
    record_model_verification(tool_calling=True, vision=False)
    payload = json.loads(model_settings_path().read_text(encoding="utf-8"))
    payload["temperature"] = 0.9
    model_settings_path().write_text(json.dumps(payload), encoding="utf-8")

    assert effective_model_settings()["verification_status"] == "stale"


def test_verification_failure_is_bounded_and_redacted() -> None:
    save_model_settings(
        base_url="https://provider.example/v1", model="model", api_key="stored-secret", supports_vision=None,
    )
    failure = record_model_verification_failure(
        code="AUTH_FAILED", message="api_key=provider-secret Bearer token-value-123456789 rejected",
    )

    assert failure["status"] == "failed"
    assert failure["last_error"] == {
        "code": "AUTH_FAILED",
        "message": "api_key=[REDACTED] Bearer [REDACTED] rejected",
    }
    persisted = json.loads(model_settings_path().read_text(encoding="utf-8"))["verification"]
    assert "provider-secret" not in json.dumps(persisted)
    assert "token-value-123456789" not in json.dumps(persisted)


def test_verification_lifecycle_exposes_verifying_and_stales_after_failed_config_change() -> None:
    save_model_settings(
        base_url="https://provider.example/v1", model="model-a", api_key="stored-secret", supports_vision=False,
    )
    started = record_model_verification_started()
    assert started["status"] == "verifying"
    failed = record_model_verification_failure(code="AUTH_FAILED", message="credential rejected")
    assert failed["status"] == "failed"

    save_model_settings(
        base_url="https://provider.example/v1", model="model-b", api_key=None, supports_vision=False,
    )
    assert effective_model_settings()["verification_status"] == "stale"


def test_chat_tools_retries_reasoning_truncation_once_with_safe_signal(monkeypatch) -> None:
    client = OpenAICompatibleClient(
        base_url="https://provider.example/v1/chat/completions", api_key="secret", model="reasoner",
        max_tokens=256, reasoning_mode="enabled",
    )
    payloads: list[dict[str, Any]] = []
    responses = iter([
        {
            "id": "first-request",
            "choices": [{"finish_reason": "length", "message": {"reasoning_content": "private chain"}}],
        },
        {
            "id": "retry-request",
            "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{"id": "call-1"}]}}],
        },
    ])

    def fake_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert url == "https://provider.example/v1/chat/completions"
        payloads.append(payload)
        return next(responses)

    monkeypatch.setattr(client, "_post_json", fake_post)
    result = client.chat_tools(
        [{"role": "user", "content": "act"}],
        tools=[{"type": "function", "function": {"name": "act", "parameters": {"type": "object"}}}],
    )

    assert [payload["max_tokens"] for payload in payloads] == [256, 768]
    assert all(payload["thinking"] == {"type": "enabled"} for payload in payloads)
    assert result["request_id"] == "retry-request"
    assert result["provider_retry"] == {
        "event": "PROVIDER_RETRY",
        "reason": "tool_call_truncated_after_reasoning",
        "attempts": 2,
        "previous_max_output_tokens": 256,
        "retry_max_output_tokens": 768,
    }
    assert "private chain" not in json.dumps(result["provider_retry"])


def test_chat_tools_never_retries_more_than_once(monkeypatch) -> None:
    client = OpenAICompatibleClient(
        base_url="https://provider.example/v1", api_key="secret", model="reasoner", max_tokens=1024,
    )
    calls = 0

    def always_truncated(_url: str, _payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"choices": [{"finish_reason": "length", "message": {"reasoning_content": "still thinking"}}]}

    monkeypatch.setattr(client, "_post_json", always_truncated)
    result = client.chat_tools([], tools=[])

    assert calls == 2
    assert result["finish_reason"] == "length"
    assert result["provider_retry"]["attempts"] == 2


@pytest.mark.parametrize(
    "choice",
    [
        {"finish_reason": "stop", "message": {"reasoning_content": "complete"}},
        {"finish_reason": "length", "message": {"content": "truncated"}},
        {"finish_reason": "length", "message": {"reasoning_content": "thinking", "tool_calls": [{"id": "call"}]}},
    ],
)
def test_chat_tools_does_not_retry_other_provider_responses(monkeypatch, choice: dict[str, Any]) -> None:
    client = OpenAICompatibleClient(base_url="https://provider.example/v1", api_key="secret", model="model")
    calls = 0

    def response_once(_url: str, _payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"choices": [choice]}

    monkeypatch.setattr(client, "_post_json", response_once)
    result = client.chat_tools([], tools=[])

    assert calls == 1
    assert "provider_retry" not in result
