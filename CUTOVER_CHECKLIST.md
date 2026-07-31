# TGA ONE-TIME CUTOVER CHECKLIST

> Source of truth: current HEAD, API contracts, schema v6, and executable tests.
> This checklist is updated during implementation. A checked item means the
> physical deletion or verification has been completed, not merely planned.

## A. Product Surface

- [x] AppShell and the 13 top-level navigation entries exist.
- [x] Dashboard uses a bounded aggregate query.
- [x] Task list uses a paged query and URL-backed filters.
- [x] Task detail is separate from Runtime.
- [x] Global approvals use original Action IDs.
- [x] Create Task is a five-step flow with a blocking preflight.
- [x] Resources is a real page backed by a bounded query.
- [x] Reports is a real page backed by a bounded query.
- [x] Knowledge Bases is a real read-only page backed by current retrieval data.
- [x] Team Templates is a real catalog page.
- [x] Solver Definitions is a real catalog page.
- [x] Skills is an independent page and states that Skills do not grant authority.
- [x] Tools & MCP is an independent page.
- [x] Models is an independent page.
- [x] Policies & Budgets is a real catalog page.
- [x] System Status is a real diagnostics page.

## B. Frontend Cutover

| Legacy object | Replacement | Delete phase | Status |
|---|---|---|---|
| `SessionRuntimePage` | `TaskRuntimePage` | D2 | deleted |
| `ReactWorkbench` | Runtime feature components | D2 | deleted |
| `SettingsPages` | independent configuration pages | D2 | deleted |
| `ProductRoutePlaceholder` | real product pages | D2 | deleted |
| `VITE_RUNTIME_PAGE` | one Runtime page | D2 | deleted |
| `/sessions/*` | `/tasks/:taskId/*` | D2 | deleted |
| old Settings aliases | formal Settings routes | D2 | deleted |
| legacy Runtime types and normalizer | schema v6 Runtime store | D2 | deleted |
| snapshot union fallback | schema v6-only parser | D2 | deleted |

- [x] `SessionRuntimePage` files and imports do not exist.
- [x] `ReactWorkbench` files and imports do not exist.
- [x] `SettingsPages` files and imports do not exist.
- [x] `ProductRoutePlaceholder` files and imports do not exist.
- [x] `VITE_RUNTIME_PAGE` does not exist in source, docs, or env examples.
- [x] Router does not register or redirect `/sessions/*`.
- [x] Router does not register old Settings aliases.
- [x] Runtime frontend model accepts schema v6 only.
- [x] Legacy normalizer, legacy view, and snapshot union types do not exist.
- [x] Legacy-only CSS and tests are deleted.
- [x] Duplicate `/tools/mcp/*` management aliases are deleted; the UI uses only `/mcp/servers/*` and `/mcp/images/*`.

## C. Runtime and Completion Cutover

- [x] Formal tool manifests expose only the supervisor task-completion proposal.
- [x] Workers use `submit_worker_result`.
- [x] Supervisors use `propose_task_completion`.
- [x] The host performs deterministic completion validation.
- [x] Every executable tool call passes through `ToolGovernanceGateway`.
- [x] The legacy Session-centric Runtime main path is physically deleted.
- [x] The long-term legacy tool adapter is physically deleted.
- [x] Runtime performs no dual read.
- [x] Runtime performs no dual write.
- [x] Runtime performs no old-schema fallback.
- [x] Schema v5 returns an explicit Migration Required error.

## D. Offline Data Migration

- [x] Migration is offline and backup-first.
- [x] Dry run writes an object mapping report.
- [x] Apply retains rollback artifacts and rolls back failed publication.
- [x] Migration writes an audit report.
- [x] Artifact provenance is preserved rather than inferred.
- [x] Evidence and Finding confirmation is never fabricated.
- [x] Post-migration integrity and target schema are verified.
- [x] CLI exposes explicit dry-run, apply, and verify operations.

## E. Final Verification

- [x] `pytest -q` (`376 passed, 2 skipped`)
- [x] `npm test`
- [x] `npm run build`
- [x] `npm run test:e2e` (`18 passed`)
- [x] `git diff --check`
- [x] 390px visual verification
- [x] 1024px visual verification
- [x] 1280px visual verification
- [x] 1440px visual verification
- [x] 1920px visual verification
- [x] Repository-wide Cutover search has no production-path matches.
- [x] Final route table exactly matches the implementation specification.

Frontend verification completed with `25` files and `85` tests. The route table
contains only `/`, `/tasks`, `/tasks/new`, `/tasks/:taskId`,
`/tasks/:taskId/runtime`, `/tasks/:taskId/replay`, `/approvals`, `/resources`,
`/reports`, `/knowledge-bases`, `/settings/teams`, `/settings/solvers`,
`/settings/skills`, `/settings/tools`, `/settings/models`,
`/settings/policies`, and `/system`.

Local acceptance smoke verified the frontend root and `/tasks/new`, API health,
Dashboard, Tasks, Approvals, Resources, Reports, Knowledge Bases, configuration
catalogs, Skills, Tools/MCP, Models, Capabilities, and Mode Profiles. Every
queried endpoint returned `200`; cross-origin responses allow the local frontend.
Invalid task payloads are skipped as unreadable current-contract data and mark
Dashboard storage degraded; they are never normalized through an old-schema
fallback.

## Pre-existing External Changes

- `.phase*`, `.pytest-*`, `tmp/pytest-*`, and local smoke-run directories were
  present before this execution. They are generated artifacts, are not modified,
  and are excluded from the deliverable.
