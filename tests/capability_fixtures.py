from __future__ import annotations

from tga.application.capabilities import CapabilityAssignmentService
from tga.domain.solver.instances import CapabilityBindingSnapshot
from tga.runtime.tooling.catalog import RuntimeToolCatalog


def assignment_service() -> CapabilityAssignmentService:
    return CapabilityAssignmentService()


def capability_ids(definition) -> tuple[str, ...]:
    service = assignment_service()
    values = [item.id for item in service.resolve_host(definition)]
    if definition.kali is not None:
        values.extend(definition.kali.capabilities)
    return tuple(values)


def capability_binding(definition) -> CapabilityBindingSnapshot:
    service = assignment_service()
    return CapabilityBindingSnapshot(
        host_capability_profile_id=definition.host_capability_profile_id,
        host_capability_ids=tuple(item.id for item in service.resolve_host(definition)),
        kali=definition.kali,
        content_sha256=service.binding_fingerprint(definition),
    )


def empty_mcp_catalog() -> RuntimeToolCatalog:
    return RuntimeToolCatalog(entries=())
