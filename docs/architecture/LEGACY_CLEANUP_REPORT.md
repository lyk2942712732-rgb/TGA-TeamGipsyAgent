# Legacy cleanup report

This report records the completed one-time schema-v6 Cutover. Historical schemas are
accepted only by the offline migration command. The application has no old Runtime, Replay,
frontend, completion, tool execution, dual-read, or dual-write path.

| Retired object | Disposition |
| --- | --- |
| `MemoryEntry`, `MemoryKind` | migration-only historical model; v6 uses TaskHint and KnowledgeItem |
| `StrategyCard`, `StrategyStep` | migration-only historical model; v6 uses GlobalPlan and LocalPlan |
| old SolverRecord | migration-only historical model; v6 uses SolverInstance |
| Task-level skill bundle | migration extraction only; current Tasks use formal Skill snapshots |
| old Runtime tables | archived by migration, then physically dropped before publication |
| old Session pages and `/sessions/*` | physically deleted with no alias or redirect |
| old frontend Runtime unions/normalizers | physically deleted; schema v6 is parsed directly |
| old completion alias | physically deleted; Supervisor proposes and Host validates |
| old tool dispatcher/recorder/strategy resolver | physically deleted |
| transcript JSON mirror and old repositories | physically deleted |

Current Session lifecycle records, Context metrics, and challenge completion state live in
`tga.domain.runtime`; they are current schema-v6 contracts, not historical Solver models.
Historical converters and the read-only v5 reader live exclusively under `tga.migrations`.

The authoritative writes are TaskSpec/Hint/Intervention, Plan/Intent, Knowledge, Evidence,
Solver, Transcript, governed Action, Approval, Retrieval, and canonical Event records.
