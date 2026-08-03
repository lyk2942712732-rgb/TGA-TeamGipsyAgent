"""Single capability assignment source for catalogs and runtime manifests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tga.application.capabilities.registry_service import HostCapabilityRegistry
from tga.application.kali import KaliProfileService
from tga.domain.capabilities import (
    HostCapabilityManifestEntry,
    KaliRuntimeManifest,
    SolverRuntimeManifest,
)
from tga.domain.kali import KaliExecArguments, KaliSessionArguments


HIGH_IMPACT_HOST_CAPABILITIES = {"artifact.publish", "input.materialize"}


class CapabilityAssignmentService:
    def __init__(
        self,
        *,
        host_registry: HostCapabilityRegistry | None = None,
        kali_profiles: KaliProfileService | None = None,
        definitions=None,
    ) -> None:
        self.host_registry = host_registry or HostCapabilityRegistry()
        self.kali_profiles = kali_profiles or KaliProfileService()
        if definitions is None:
            from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
            definitions = SolverDefinitionRegistry.builtin(
                host_registry=self.host_registry,
                kali_profiles=self.kali_profiles,
            )
        self.definitions = definitions

    def resolve_host(self, definition) -> tuple[HostCapabilityManifestEntry, ...]:
        profile = self.host_registry.require_profile(definition.host_capability_profile_id)
        selected = set(profile.capability_ids)
        selected.update(definition.host_capability_overrides.add)
        selected.difference_update(definition.host_capability_overrides.remove)
        values: list[HostCapabilityManifestEntry] = []
        for capability_id in sorted(selected):
            capability = self.host_registry.require(capability_id)
            if definition.orchestration_role not in capability.allowed_roles:
                raise ValueError(
                    f"Host capability {capability_id} does not allow role "
                    f"{definition.orchestration_role}"
                )
            source = (
                "solver_add"
                if capability_id in definition.host_capability_overrides.add
                else definition.host_capability_profile_id
            )
            values.append(HostCapabilityManifestEntry(
                id=capability.id,
                provider_tool_name=capability.id.replace(".", "_"),
                display_name=capability.display_name,
                category=capability.category,
                description=capability.description,
                risk=capability.risk,
                input_schema=capability.input_schema,
                output_schema=capability.output_schema,
                handler_key=capability.handler_key,
                source=source,
            ))
        return tuple(values)

    def resolve_host_snapshot(
        self, capability_ids: tuple[str, ...], *, role: str, source: str
    ) -> tuple[HostCapabilityManifestEntry, ...]:
        values: list[HostCapabilityManifestEntry] = []
        for capability_id in capability_ids:
            capability = self.host_registry.require(capability_id)
            if role not in capability.allowed_roles:
                raise ValueError(
                    f"Host capability {capability_id} does not allow role {role}"
                )
            values.append(HostCapabilityManifestEntry(
                id=capability.id,
                provider_tool_name=capability.id.replace(".", "_"),
                display_name=capability.display_name,
                category=capability.category,
                description=capability.description,
                risk=capability.risk,
                input_schema=capability.input_schema,
                output_schema=capability.output_schema,
                handler_key=capability.handler_key,
                source=source,
            ))
        return tuple(values)

    def resolve_kali(self, definition) -> KaliRuntimeManifest | None:
        binding = definition.kali
        if binding is None:
            return None
        profile = self.kali_profiles.verify_binding(
            binding.profile_id, binding.capabilities
        )
        return KaliRuntimeManifest(
            profile_id=profile.id,
            image_name=profile.image_name,
            image_tag=profile.image_tag,
            image_digest=profile.image_digest,
            capabilities=binding.capabilities,
            allowed_executables=profile.allowed_executables,
            session_executables=profile.session_executables,
            network_mode=profile.network_mode,
            limits=profile.limits.model_dump(mode="json"),
        )

    def manifest(
        self,
        *,
        task_id: str,
        solver_id: str,
        definition,
        intent_id: str | None,
        policy_fingerprints: tuple[str, ...] = (),
        mcp_entries: tuple[Any, ...] = (),
        execution_policy=None,
        capability_snapshot=None,
    ) -> SolverRuntimeManifest:
        frozen_host_capabilities = getattr(capability_snapshot, "host_capabilities", ())
        if capability_snapshot is not None and frozen_host_capabilities:
            host_capabilities = frozen_host_capabilities
        elif capability_snapshot is not None:
            # Legacy snapshots cannot carry the complete contract. Keep this
            # fallback for projections/tests; AgentSession rejects such records
            # before starting a production run.
            host_capabilities = self.resolve_host_snapshot(
                capability_snapshot.host_capability_ids,
                role=definition.orchestration_role,
                source=capability_snapshot.host_capability_profile_id,
            )
        else:
            host_capabilities = self.resolve_host(definition)
        if execution_policy is not None:
            high_impact = execution_policy.high_impact
            allowed_actions = {item.casefold() for item in high_impact.allowed_actions}
            host_capabilities = tuple(
                item
                for item in host_capabilities
                if item.id not in HIGH_IMPACT_HOST_CAPABILITIES
                or high_impact.mode == "approval_required"
                or (
                    high_impact.mode == "allowlisted"
                    and item.id.casefold() in allowed_actions
                )
            )
        kali = (
            capability_snapshot.kali_runtime
            if capability_snapshot is not None
            and capability_snapshot.kali_runtime is not None
            else self.resolve_kali(
                definition.model_copy(update={"kali": capability_snapshot.kali})
                if capability_snapshot is not None
                else definition
            )
        )
        if (
            kali is not None
            and execution_policy is not None
            and execution_policy.local_compute.mode == "disabled"
        ):
            kali = None
        return SolverRuntimeManifest(
            task_id=task_id,
            solver_id=solver_id,
            solver_definition_id=definition.id,
            intent_id=intent_id,
            host_capabilities=host_capabilities,
            kali=kali,
            policy_fingerprints=policy_fingerprints or (definition.content_sha256,),
            mcp_entries=mcp_entries,
        )

    def definition_detail(self, definition) -> dict[str, Any]:
        payload = definition.model_dump(mode="json")
        payload["role"] = payload.pop("orchestration_role")
        payload["host_capabilities"] = [
            {
                "id": item.id,
                "display_name": item.display_name,
                "category": item.category,
                "risk": item.risk,
                "source": item.source,
            }
            for item in self.resolve_host(definition)
        ]
        kali = self.resolve_kali(definition)
        payload["kali"] = None if kali is None else {
            **kali.model_dump(mode="json"),
            "tools": [
                item.model_dump(mode="json")
                for item in self.kali_profiles.require(kali.profile_id).tools
            ],
        }
        return payload

    def solvers_for_host(self, capability_id: str) -> tuple[str, ...]:
        self.host_registry.require(capability_id)
        return tuple(
            definition.id
            for definition in self.definitions.all()
            if capability_id in {item.id for item in self.resolve_host(definition)}
        )

    def solvers_for_kali_capability(self, capability_id: str) -> tuple[str, ...]:
        if capability_id not in {"kali.exec", "kali.session"}:
            raise KeyError(capability_id)
        return tuple(
            definition.id
            for definition in self.definitions.all()
            if definition.kali is not None
            and capability_id in definition.kali.capabilities
        )

    def solvers_for_kali_profile(self, profile_id: str) -> tuple[str, ...]:
        self.kali_profiles.require(profile_id)
        return tuple(
            definition.id
            for definition in self.definitions.all()
            if definition.kali is not None and definition.kali.profile_id == profile_id
        )

    @staticmethod
    def binding_fingerprint(definition) -> str:
        payload = {
            "host_capability_profile_id": definition.host_capability_profile_id,
            "host_capability_overrides": definition.host_capability_overrides.model_dump(mode="json"),
            "kali": definition.kali.model_dump(mode="json") if definition.kali else None,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def kali_schema(capability_id: str) -> dict[str, Any]:
        model = {
            "kali.exec": KaliExecArguments,
            "kali.session": KaliSessionArguments,
        }.get(capability_id)
        if model is None:
            raise KeyError(capability_id)
        return model.model_json_schema()


__all__ = ["CapabilityAssignmentService"]
