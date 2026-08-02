from __future__ import annotations

from tga.contracts import (
    ExecutionPolicy,
    HighImpactExecutionPolicy,
    LocalComputeExecutionPolicy,
    MCPCapabilitySnapshot,
    MCPCapabilityTool,
    NetworkExecutionPolicy,
    TGATask,
    default_mode_config,
)
from tga.modes import normalize_mode
from tga.network_policy import normalize_origin
from tga.models.settings import record_model_verification, save_model_settings


MODEL_SNAPSHOT = {
    "provider": "openai-compatible",
    "model": "test-model",
    "capability_fingerprint": "f" * 64,
    "verification_id": "verify_test_provider",
    "verified_at": "2026-01-01T00:00:00Z",
    "capabilities": {"tool_calling": True, "vision": False},
    "max_output_tokens": 4096,
    "timeout_seconds": 60,
    "temperature": 0.0,
    "reasoning_mode": "auto",
}


def task(**overrides) -> TGATask:
    """Build a valid schema-v6 task for tests.

    `TGATask` requires mode_config, execution_policy and model_snapshot so no
    persisted task can silently acquire defaults.  Those creation-time values
    are supplied here explicitly instead of by a model validator.
    """
    mode = normalize_mode(overrides.pop("mode", "ctf"))
    payload: dict = {
        "id": "task_test",
        "name": "test task",
        "mode": mode,
        "goal": "test goal",
        **overrides,
    }
    if not payload.get("model_snapshot"):
        # When a verified provider is configured, mirror it so runtime accepts
        # the task; otherwise fall back to a static snapshot for pure model tests.
        try:
            payload["model_snapshot"] = verified_model_snapshot()
        except Exception:
            payload["model_snapshot"] = dict(MODEL_SNAPSHOT)
    if not payload.get("mode_config"):
        payload["mode_config"] = default_mode_config(
            mode, flag_format=payload.get("flag_format")
        ).model_dump(mode="json")
    if not payload.get("execution_policy"):
        payload["execution_policy"] = ExecutionPolicy().model_dump(mode="json")
    return TGATask.model_validate(payload)


def configure_verified_model(monkeypatch) -> None:
    """Create an isolated verified provider configuration for API tests.

    Product code deliberately rejects environment-only provider settings because
    they cannot carry a matching persisted capability verification record.
    """

    for name in (
        "TGA_LLM_API_KEY", "TGA_LLM_BASE_URL", "TGA_LLM_MODEL",
        "TGA_LLM_SUPPORTS_VISION", "TGA_LLM_MAX_OUTPUT_TOKENS",
        "TGA_LLM_TIMEOUT_S", "TGA_LLM_TEMPERATURE", "TGA_LLM_REASONING_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    save_model_settings(
        base_url="https://model.test/v1",
        model="test-model",
        api_key="test-key",
        supports_vision=False,
    )
    record_model_verification(tool_calling=True, vision=False, verification_id="verify_test_provider")


def verified_model_snapshot() -> dict:
    """Build a model snapshot that matches the recorded verification record.

    Runtime rejects a task whose snapshot fingerprint differs from the current
    verification, so tests that actually run a session must derive the snapshot
    from `model_config_status()` instead of hard-coding it.
    """
    from tga.models.bootstrap import model_config_status

    status = model_config_status()
    verification = status.get("verification") or {}
    return {
        "provider": status.get("provider") or "openai-compatible",
        "model": status.get("model") or "test-model",
        "capability_fingerprint": verification.get("capability_fingerprint") or "f" * 64,
        "verification_id": verification.get("id") or "verify_test_provider",
        "verified_at": verification.get("verified_at") or "2026-01-01T00:00:00Z",
        "capabilities": verification.get("capabilities") or {},
        "max_output_tokens": status.get("max_output_tokens") or 4096,
        "timeout_seconds": status.get("timeout_seconds") or 60,
        "temperature": status.get("temperature") or 0.0,
        "reasoning_mode": status.get("reasoning_mode") or "auto",
    }


def _origins(scopes: list[str]) -> list[str]:
    origins: list[str] = []
    for value in scopes:
        candidate = value if "://" in value else f"http://{value}"
        origin = normalize_origin(candidate)
        if origin not in origins:
            origins.append(origin)
    return origins


def execution_policy(
    scopes: list[str] | None = None,
    *,
    network_mode: str = "interact",
    process: bool = False,
) -> ExecutionPolicy:
    origins = _origins(scopes or [])
    return ExecutionPolicy(
        preset="custom",
        network=NetworkExecutionPolicy(
            access="task_sources" if origins else "disabled",
            interaction=network_mode,
            seed_origins=origins,
            deny_private_networks=False,
            deny_loopback=False,
            deny_link_local=False,
        ),
        local_compute=LocalComputeExecutionPolicy(mode="isolated" if process else "disabled"),
        high_impact=HighImpactExecutionPolicy(mode="forbidden"),
    )


def mcp_snapshot(snapshot, server_id: str) -> MCPCapabilitySnapshot:
    routes = [route for route in snapshot.routes if route.server_id == server_id]
    return MCPCapabilitySnapshot(
        catalog_version=snapshot.version,
        server_ids=[server_id],
        tools=[MCPCapabilityTool(**route.model_dump(mode="json")) for route in routes],
    )


def mcp_policy(server_id: str, *, allow_active: bool = False) -> ExecutionPolicy:
    del server_id
    return ExecutionPolicy(
        preset="custom",
        local_compute=LocalComputeExecutionPolicy(mode="isolated" if allow_active else "disabled"),
    )
