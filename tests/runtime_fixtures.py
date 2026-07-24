from __future__ import annotations

from tga.contracts import (
    ExecutionPolicy,
    HighImpactExecutionPolicy,
    LocalComputeExecutionPolicy,
    MCPCapabilitySnapshot,
    MCPCapabilityTool,
    NetworkExecutionPolicy,
)
from tga.network_policy import normalize_origin
from tga.models.settings import record_model_verification, save_model_settings


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
