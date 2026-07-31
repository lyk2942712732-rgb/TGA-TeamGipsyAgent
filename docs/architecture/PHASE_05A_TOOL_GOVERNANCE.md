# Phase 5A tool governance

`ToolGovernanceGateway` is the sole model-initiated tool boundary. Providers submit only a
non-authoritative `ModelToolIntent`; the Host injects durable Task, Solver, Intent, plan-step,
policy-snapshot, Skill-snapshot, and attempt identity.

The gateway owns authorization, Approval, Action lifecycle, idempotency, persistent budgets,
resource locks, direct pinned MCP routing, and bounded observations. It revalidates authority
immediately before I/O. Execution adapters perform argument validation, transport, and
Artifact publication but do not write governance state.

Role-scoped manifests expose task completion only to Supervisors and execution tools only to
Workers. The accepted effect metadata field is `proposed_effect`; the former metadata alias,
single-Solver policy profile, old handler interface, aggregate MCP tool, and executable
schema-v5 dispatcher were deleted.

Schema v5 cannot dispatch or replay through Runtime. It is readable only inside the explicit
offline migration package.
