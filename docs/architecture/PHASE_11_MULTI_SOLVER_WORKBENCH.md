# Phase 11: multi-Solver command workbench

Phase 11 turns the normalized Phase-10 runtime page into the task command
workbench. The browser remains a projection consumer: it does not infer
orchestration state from presentation text and it never reconstructs or shows
hidden model reasoning.

## Command surface and navigation

The command header summarizes Task mode/status, Intent completion, active /
completed / blocked Solvers, Task Token / Tool / Artifact usage, pending
Approvals, elapsed time, and SSE health. Task-level pause, resume, cancel,
intervention, Approval Center, replay, and report actions share the same shell.
Replay and schema-v5 views are read-only.

The left Team Explorer renders only instantiated durable Solver records in a
Supervisor-to-Worker/Reviewer/Reporter tree. Cards expose role, specialties,
current Intent, latest projected activity, Skill count, usage, and governance
signals. The central workspace has stable Task Overview, Work Items, Activity
Timeline, Evidence and Findings, Resources, and Approval Center tabs. URL
parameters continue to preserve Solver, Intent, and tab selection.

Work Items provide Kanban, a bounded dependency view, and a list over the
eight durable Intent states. Timeline rows come only from canonical persisted
events and can be filtered by Solver, Intent, and event type or separated into
Solver lanes. Evidence and resource views preserve owner and provenance fields
and expose shared publications and bounded retrieval summaries without
rendering Solver-private content bodies.

## Inspection and governance

The Solver Inspector has Overview, Transcript, Local Plan, Knowledge, Skills,
Tools, Artifacts, and Config views. Transcript rows are paged per selected
Solver and can be located by turn or tool-call identifier. Concise mode shows
event summaries; protocol mode shows sanitized persisted payloads. Keys that
could contain hidden reasoning are removed. When the backend does not project a
local plan or transcript endpoint, the UI states that absence instead of
deriving one from unrelated fields.

Approval Center presents the full pending queue and supports one-time approve
or reject decisions only. Intervention supports task, Supervisor, Solver, and
Intent UI scopes plus hint, instruction, constraint, priority change, and
answer kinds. The Supervisor choice is translated to the backend's Solver
scope with the durable Supervisor ID. The dialog warns that interventions do
not override policy, trust boundaries, existing constraints, or approval
requirements.

## Mode scenes and replay

One projection-driven scene shell supplies focused summaries for CTF,
penetration testing, incident response, vulnerability research, and reverse
engineering Tasks. These views read snapshot flags, artifact indexes,
findings, evidence, retrieval, and Task metadata; they do not introduce a
second source of truth.

Replay rebuilds Solver, Intent, plan, and Approval state from the available
bounded canonical event window at a selected sequence. It disables live SSE,
governance controls, and interventions. Schema-v5 replay continues through the
single legacy adapter. A bounded window is intentionally not presented as a
complete historical transcript when older events are no longer loaded.

## Performance envelope

- sequential SSE envelopes are reduced in animation-sized batches;
- the normalized in-memory event window remains bounded to 500;
- the visible timeline and dependency graph are bounded to 100 records/nodes;
- Solver transcript pages render 20 events at a time;
- artifact bodies stay lazy and views use entity selectors instead of scanning
  transcript prose.

Phase 12 may remove the legacy single-Agent frontend and compatibility seams
only after their migration and replay obligations have been audited.
