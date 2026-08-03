"""Frozen capability surface for one Solver assignment."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tga.domain.capabilities.solver_binding import KaliCapability


class HostCapabilityManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    provider_tool_name: str
    display_name: str
    category: str
    description: str
    risk: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler_key: str
    source: str


class KaliRuntimeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    image_name: str
    image_tag: str
    image_digest: str | None = None
    capabilities: tuple[KaliCapability, ...]
    allowed_executables: tuple[str, ...]
    session_executables: tuple[str, ...]
    network_mode: str
    limits: dict[str, Any]


class SolverRuntimeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    solver_id: str
    solver_definition_id: str
    intent_id: str | None = None
    host_capabilities: tuple[HostCapabilityManifestEntry, ...]
    kali: KaliRuntimeManifest | None = None
    policy_fingerprints: tuple[str, ...]
    mcp_entries: tuple[Any, ...] = Field(default_factory=tuple)

    @property
    def provider_names(self) -> tuple[str, ...]:
        host = tuple(item.provider_tool_name for item in self.host_capabilities)
        kali = tuple(
            capability.replace(".", "_") for capability in self.kali.capabilities
        ) if self.kali is not None else ()
        mcp = tuple(getattr(item, "provider_tool_name", "") for item in self.mcp_entries)
        return host + kali + tuple(name for name in mcp if name)

    @property
    def entries(self) -> tuple[Any, ...]:
        """Build ephemeral gateway routes without serializing router metadata."""
        from tga.application.capabilities.assignment_service import (
            CapabilityAssignmentService,
        )
        from tga.runtime.tooling.catalog.definitions import ToolCatalogEntry

        values: list[Any] = []
        control_categories = {"orchestration", "result", "review", "reporting"}
        for item in self.host_capabilities:
            tool_class = "control" if item.category in control_categories else (
                "retrieval" if item.category == "retrieval" else "resource_read"
            )
            values.append(ToolCatalogEntry(
                provider_tool_name=item.provider_tool_name,
                capability=item.id,
                tool_class=tool_class,
                backend="host_control" if tool_class == "control" else "host_retrieval",
                description=item.description,
                parameters=item.input_schema,
                risk=item.risk,
                max_read_chars=262_144 if tool_class in {"resource_read", "retrieval"} else None,
            ))
        if self.kali is not None:
            for capability in self.kali.capabilities:
                values.append(ToolCatalogEntry(
                    provider_tool_name=capability.replace(".", "_"),
                    capability=capability,
                    tool_class="execution",
                    backend="sandbox",
                    description=(
                        "Execute one allowlisted Kali program."
                        if capability == "kali.exec"
                        else "Manage an interactive Kali PTY session."
                    ),
                    parameters=CapabilityAssignmentService.kali_schema(capability),
                    risk="active",
                    execution_profile_id=self.kali.profile_id,
                ))
        values.extend(self.mcp_entries)
        return tuple(values)

    def get(self, provider_name: str) -> Any | None:
        return next(
            (item for item in self.entries if item.provider_tool_name == provider_name),
            None,
        )


__all__ = [
    "HostCapabilityManifestEntry",
    "KaliRuntimeManifest",
    "SolverRuntimeManifest",
]
