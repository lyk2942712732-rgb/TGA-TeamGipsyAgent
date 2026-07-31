# Phase 10: frontend data foundation

Phase 10 introduces a task-level frontend data boundary without attempting the
final Phase-11 visual workbench. The default `/tasks/:taskId/runtime` route now
loads a normalized schema-v6 `RuntimeStore`; the previous Session Runtime page was
physically removed by the one-time Task Runtime cutover.

## Boundary

`features/runtime/models/normalize.ts` accepts schema-v6 task snapshots only.
React components do not branch on schema versions. The former legacy view,
normalizer, snapshot union, page, CSS, and tests were physically deleted.

The store owns normalized entity maps:

```text
task, session, team
solversById, intentsById, workerResultsById
knowledgeById, artifactsById, evidenceById, findingsById
actionsById, approvalsById, retrievalById, eventsBySeq
```

Selectors provide Supervisor, active Solver, Solver tree, runnable Intent,
pending Approval, Task budget, Solver/Intent event, confirmed Finding, and
Knowledge conflict views. Components select by stable IDs; no array position
represents the active Solver.

## Incremental events

`useTaskRuntime` performs one authoritative Snapshot load, database event
catch-up, and SSE subscription. Duplicate sequences are idempotent. A sequence
gap fetches paginated events until the server cursor is reached; an
unrecoverable gap or an event with an intentionally incomplete projection
triggers a debounced authoritative Snapshot refresh.

The v6 reducer handles Solver, Intent, WorkerResult, GlobalPlan, Knowledge,
EvidenceClaim, Approval, Retrieval and completion events. Entity updates use
the payload entity version when present and otherwise use the event sequence.
Unknown schema-v6 events remain in the timeline without changing domain state.

The in-memory event map is bounded to 500 records, and the timeline renders a
window of at most 100 records. This prevents an SSE session from creating an
unbounded DOM or browser store.

## Navigable skeleton

The Phase-10 page composes `TaskCommandHeader`, `TeamExplorer`,
`TaskWorkspaceTabs`, `SolverInspector`, and `GlobalActionDock`. Workspace tabs
already read real normalized Intent, event, evidence, approval, and retrieval
state. Query parameters preserve `solver`, `intent`, and `tab` selections on
refresh. Native buttons, tree/treeitem semantics, tabs/tabpanels, textual
status labels, non-focusing live updates, and mobile side-panel drawers form
the accessibility baseline.

Phase 11 may rely on:

- the model types and `normalizeRuntimeSnapshot` exported from
  `features/runtime/models`;
- all selector functions in `models/selectors.ts`;
- `reduceRuntimeEvent`, `mergeRuntimeEvents`, and `useTaskRuntime`;
- the `solver`, `intent`, and `tab` URL contract;
- the five page-shell components and feature folders established here.
