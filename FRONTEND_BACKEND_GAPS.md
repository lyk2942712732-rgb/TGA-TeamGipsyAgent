# Frontend / Backend Capability Gaps

This document records backend capabilities still required by the desktop product UI. Production pages continue to use real APIs. Visual and component tests may use fixtures, but the application never presents fixture records as production data.

Capability states are centralized in `apps/web/src/api/capability-state.ts`:

- `available`: a real read or write API exists and is used.
- `read_only`: real data can be read, but the corresponding write workflow is unavailable.
- `unsupported`: no authoritative backend capability exists yet.

## 01 Dashboard

- Available: `GET /api/v2/dashboard` for metrics, attention items, active tasks, completed tasks, and system signals.
- Unsupported: an independent recent confirmed-results feed, including result type, confirmation actor, confirmation time, and task linkage.

## 02 Tasks

- Available: `GET /api/v2/tasks` and `DELETE /api/v2/tasks/{task_id}`.
- Gap: no additional API is required for the implemented desktop table. Server-side card/table preference persistence is not available and is intentionally local.

## 03 Create Task

- Available: mode profiles, input upload/delete, Skill preview, preflight, task creation, model state, and MCP health.
- Unsupported persistence: separate `Instructions`, `Constraints`, `Success Criteria`, and target `URL` fields in the create request.
- Current behavior: all four controls exist as clearly marked frontend drafts. `Objective` maps to `goal`; prompt and files map to `input`. Draft-only fields are never silently merged into another field and never reported as saved.
- Needed contract: extend preflight and create schemas with typed directive arrays/fields and return their frozen snapshot.

## 04 Task Detail

- Available: task detail, team, inputs, evidence, and timeline endpoints.
- Gap: no blocking API gap. Large tab payloads are loaded only when the corresponding tab opens.

## 05 Runtime Workbench

- Available: session snapshot, events/page/stream, team and Solver projection, intents, evidence, approvals, task control, intervention, Solver control, and Intent retry.
- Gap: no blocking API gap for the implemented workbench. Full persisted Solver transcript and Local Plan prose are not independently projected; the UI shows only persisted event summaries and marks absent content honestly.

## 06 Approvals

- Available: global approval list and task-level decisions using the authoritative `action_id`.
- Gap: no blocking API gap. Bulk decision and reusable approval-policy editing are not available and are not exposed as successful actions.

## 07 Resources

- Read only: `GET /api/v2/catalog/resources` and task evidence projections.
- Needed: cross-task Artifact preview/search-section/download metadata as a typed resource API, Knowledge detail projection, and stable evidence-chain navigation endpoints from Finding to Claim to Artifact locator.
- Existing task-scoped Artifact retrieval remains usable where an authoritative task and Artifact ID are known.

## 08 Reports

- Available: report catalog read, task report view, and report export.
- Unsupported: draft CRUD, create-report resource, reviewing/final state transitions, version history, version restore, and report deletion.

## 09 Knowledge Bases

- Read only: `GET /api/v2/catalog/knowledge-bases`.
- Unsupported: Knowledge Base CRUD, Source CRUD, document management, index snapshot management, manual synchronization, synchronization history, and retrieval-test HTTP API.

## 10 Team Templates

- Read only: `GET /api/v2/catalog/teams` for built-in templates.
- Unsupported: create, edit, clone, archive, version history, version publish, and version restore.

## 11 Solver Definitions

- Read only: `GET /api/v2/catalog/solvers` for the built-in definition registry.
- Unsupported: create, edit, clone, archive, validation, version history, publish, and restore.

## 12 Skills

- Available: list, detail, Markdown import, update, and delete/disable.
- Frontend-derived: Category is a deterministic mapping from Skill tags/name and is labeled as such.
- Unsupported: parameter schema, dependency graph, usage statistics, usage history, and version history APIs.

## 13 Tools & MCP

- Read only: Capability registry and tools health.
- Unsupported for Capabilities: independent enable/disable, approval-rule editing, execution-limit editing, parameter-schema detail, and usage statistics.
- Available for MCP: managed-server list/create/update/delete/refresh, connection and discovery tests, method tests, image inspection, and local image import.

## 14 Models

- Available: one OpenAI-compatible Provider configuration, save, and verification.
- Unsupported: multiple Providers, Provider switching/deletion, Model Profiles, role routing, validation-history list, and profile/version management.

## 15 Policies & Budgets

- Read only: `GET /api/v2/catalog/policies`; task creation accepts `execution_policy` and `execution_budget` snapshots.
- Unsupported: policy catalog CRUD, clone, version history, version publish/restore, Tool Policy catalog, Budget Template catalog, and Retention Policy catalog.

## 16 System Status

- Available probes: API health, current model settings/verification, tools health, Capability registry, and service-specific MCP refresh/test.
- Unsupported: Scheduler diagnostics, Database health, Artifact Store health, Retrieval/Vector Index health, global Event Stream health, CPU/memory/disk metrics, alert list/history, unified diagnostics bundle, and global MCP/index refresh operations.

## Cross-Cutting Gaps

- Global search and notification-center APIs are not available; shell controls remain disabled with explicit titles.
- Capability state should eventually be returned by backend discovery metadata rather than maintained only in the frontend registry.
- Catalog endpoints should publish versioned typed contracts so adapters no longer need to normalize heterogeneous `raw` payloads.
