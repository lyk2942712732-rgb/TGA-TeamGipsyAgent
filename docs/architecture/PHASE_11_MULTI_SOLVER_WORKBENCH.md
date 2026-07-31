# Phase 11 multi-Solver command workbench

The Task Runtime is the only live workbench. It renders durable Team, Intent, Event, Evidence,
Resource, Approval, and Solver projections and never reconstructs hidden reasoning.

The left Team Explorer shows instantiated SolverInstances. The central workspace provides
Overview, Work Items, Timeline, Evidence, Resources, and Approval views. The Solver Inspector
shows bounded per-Solver projections. URL parameters preserve selected Solver, Intent, tab,
and replay sequence.

Replay is schema-v6 only and reduces canonical events into the same normalized store. It
disables all mutation controls. There is no schema-v5 view or frontend adapter.

The old single-Agent page, Session routes, Runtime union types, fallback normalizer, feature
flag, and compatibility CSS/tests were physically deleted during Cutover.
