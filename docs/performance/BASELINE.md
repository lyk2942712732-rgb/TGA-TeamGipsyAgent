# Phase 12 performance baseline

Measured 2026-07-30 on the local Windows development host. Backend fixtures use
a temporary local SQLite database, 1,000 durable Events, 2,001 Transcript
messages, two Workers, one Artifact, and one trusted global KnowledgeBase. No
external network or dangerous operation was used. Values are engineering
baselines, not cross-machine service-level guarantees.

Command:

```powershell
python scripts\benchmark_phase12.py --output output\phase12-performance.json
```

| Path | Samples | Median | p95 / elapsed |
| --- | ---: | ---: | ---: |
| Runtime Snapshot query | 20 | 7.987 ms | 9.758 ms p95 |
| Event pagination, 200 rows | 50 | 8.494 ms | 11.012 ms p95 |
| SSE process-bus wake path | 20 | 0.107 ms | 0.363 ms p95 |
| two Worker dispatch | 1 | n/a | 14.980 ms elapsed |
| long Transcript Context build | 20 | 0.657 ms | 0.821 ms p95 |
| Artifact lookup | 100 | 0.006 ms | 0.007 ms p95 |
| RAG retrieval | 10 | 0.223 ms | 0.475 ms p95 |

Frontend 10k Event command:

```powershell
cd apps\web
npm test -- --run src/features/runtime/models/performance.test.ts --reporter=verbose --silent=false
```

The normalized reducer processed 10,000 sequential Events in **620.902 ms** and
retained exactly the configured 500-Event window. The test uses a generous
5-second regression ceiling to avoid treating development-host jitter as a
product failure.

## Interpretation

Snapshot and Event query cost is currently dominated by bounded SQLite/model
projection work. No result justifies a language rewrite. If scale exceeds the
documented local envelope, candidate seams are projection caching, event-page
serialization, transcript summarization, and retrieval indexing—not domain or
governance removal.

Raw backend output is retained at `output/phase12-performance.json` for this
worktree verification run.

## Release verification commands

The release gate additionally runs `python -m pytest -q`, `npm test`,
`npm run build`, `npm run test:e2e`, architecture/static checks, and a migration
dry-run. The final local run completed with 374 backend tests passed and 2
skipped, 94 frontend tests passed, 11 browser E2E tests passed, and a successful
production build. The migration dry-run left the schema-v5 fixture unchanged
and emitted a planned report without a backup path, as required.
