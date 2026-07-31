# Migration plan

The architecture migration is complete. The one-time Cutover produced these current owners:

| Concern | Current owner |
| --- | --- |
| Task and lifecycle contracts | `tga/domain/task`, `tga/domain/runtime` |
| Solver identity and orchestration | `tga/domain/solver`, `tga/runtime/orchestration` |
| Plans and Knowledge | `tga/domain/planning`, `tga/domain/knowledge` |
| Governed Actions | `tga/runtime/tooling`, `governed_actions` persistence |
| Runtime projections | application Commands and Queries |
| Product UI | schema-v6 Task pages and normalized Runtime store |
| Historical schema conversion | `tga/migrations` only |

The application does not keep compatibility adapters after Cutover. `MemoryEntry`,
`StrategyCard`, and historical Runtime tables are available only to offline migration code.
Migration preserves a byte-for-byte backup and a separate historical Runtime archive, then
drops retired tables before publishing schema v6.

Operators use `tga migrate --backup --dry-run`, `tga migrate --apply`, and
`tga migrate --verify`. Runtime fallback is not a recovery mechanism.
