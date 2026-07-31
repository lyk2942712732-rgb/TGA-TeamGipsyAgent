# Phase 3 Solver and team resources

> Historical phase record, updated to describe the final resource contract.

Built-in SolverDefinitions and TeamTemplates are immutable, validated resources. Each current
SolverInstance freezes its Definition, model, ToolPolicy, budget, completion authority, and
`SolverSkillSnapshot`. Task-wide methods use `TaskCommonSkillSnapshot`.

Skills provide guidance and never grant authority. Capability requirements must already be
allowed by both the Runtime catalog and the frozen ToolPolicy.

Historical Task-level skill bundles are not accepted by current Tasks. The field is read only
inside schema-v5 offline migration and converted into current snapshots without affecting
Runtime authorization.
