# Frontend workbench

The default `/tasks/:taskId/runtime` page is a Task command workbench. One
normalizer maps schema-v6 and schema-v5 read-only data into ID-keyed entities.
Components never choose `solvers[0]` and never infer state from Transcript.

- Task header: progress, Solver counts, budgets, Approval, SSE and controls.
- Team Explorer: instantiated Supervisor/Worker/Reviewer/Reporter tree.
- Intent Board: Kanban, bounded dependency graph and list.
- Timeline: canonical global or Solver-lane Event views.
- Evidence/Resources: provenance-preserving domain projections.
- Solver Inspector: Transcript, Local Plan, Knowledge, Skills, Tools and Artifacts.
- Approval Center: multiple independent one-time decisions.

Five TaskMode scenes share the same projection-only shell. Replay disables SSE
and writes while restoring Solver, Intent, Plan and Approval state by sequence;
schema-v5 Replay stays in the legacy adapter.

Bounds are 500 cached Events, 100 Timeline rows/graph nodes, 20 Transcript rows
per page, lazy Artifact bodies and batched SSE updates. Responsive Team Explorer
and Inspector drawers reuse the same Store.
