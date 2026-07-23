# TGA governed AgentSession architecture improvement plan

Status: implemented migration contract and acceptance record  
Primary evidence: `docs/TASK_C3F31F2D6265_ANALYSIS.md`  
Reference reviewed: BreachWeave Manager / Solver / Observer, Idea / Memory,
timeline, session and compaction implementations

## 1. Decision and invariants

TGA keeps its Python/FastAPI controlled execution base and restores the safety
properties that the native AgentSession branch bypassed.  The target, explicit
task scope, capability schemas, risk policy, Evidence/Artifact ownership,
Finding/Flag gates, append-only events and evaluation suite remain authoritative.
Neither the Web projection nor an Observer can execute tools, write arbitrary
facts, or mark a challenge solved.

The target shape is:

```text
Task / Challenge
  -> Manager (scope, strategy ledger, scheduling, risk and completion gates)
       -> Solver AgentSession (durable transcript/workspace, controlled tools)
       -> deterministic Observer sidecar (bounded read-only input, patch only)
       -> Strategy Board (candidate strategy + hypotheses + evidence memory)
       -> Evidence / Artifact (immutable raw facts and provenance)
       -> Event projection (API, Web timeline, report and eval metrics)
```

Dependencies remain one-way: Web/API projections call the application service;
the service calls Manager; Manager owns governance and invokes the controlled
executor; capabilities return results but never mutate Board or completion state.

## 2. Current-to-target module mapping

| Concern | Current module | Target responsibility |
| --- | --- | --- |
| Session lifecycle | `tga/runtime/manager.py` | Create Board before Solver, govern actions, apply Observer patches, own stop/cleanup |
| Native tool loop | `tga/runtime/agent_session.py` | Work from bounded context; attach every action to a strategy step and expected evidence |
| Strategy / Memory | `tga/runtime/board.py` | Persist candidate `StrategyCard`, step state, hypotheses and evidence-linked memory |
| Hint ingestion | new `tga/runtime/strategy.py` | Scope-check URLs, persist raw source, extract readable segments, create candidate strategy |
| Artifact retrieval | new `tga/evidence/indexing.py`, `artifact.inspect` | Type-aware summaries and keyword/section/offset retrieval without repeated headers |
| HTTP state | new `tga/capabilities/http_session.py` | In-memory task+solver+origin CookieJar isolation and safe metadata only |
| HTTP semantics | `tga/capabilities/schemas.py`, `http.py`, `runtime.py` | Explicit body format, preflight assertions and marker checks before/after network I/O |
| Observer | `tga/runtime/observer.py` | Event-triggered deterministic patch generation; Manager validates and applies |
| Completion | `tga/runtime/completion.py` | One Flag provenance gate shared by native and legacy paths |
| Context | new `tga/runtime/context.py` | Separate immutable audit transcript from bounded model working context |
| API / Web | `apps/api`, `apps/web` | Read-only projection of strategy, timeline, safe HTTP metadata and context metrics |

## 3. Durable data migration

The migration is additive and safe for existing v2 databases:

- `strategy_cards` stores schema-versioned card JSON.  Source conclusions are
  always `candidate` until an action result verifies a step.
- `artifact_indexes` stores summaries and source-located segments; raw bytes stay
  in the existing content-addressed Artifact store.
- action governance columns store strategy/step IDs, expected outcome, retry
  reason and alternative-path analysis. Existing rows read with empty defaults.
- `context_metrics` stores bounded size/retrieval/provider-usage telemetry.
- no Cookie value is persisted. `http_session_profiles` is a runtime projection
  derived from safe in-memory counters only; process restart is explicitly marked
  as a session rebuild.

No migration updates historical task payloads, actions, flags or Artifacts. Opening
an old run lazily creates only missing schema objects. In particular,
`task_c3f31f2d6265` remains read-only and is never scheduled.

## 4. Event contract

New events are additive to schema v2:

- `HINT_FETCH_STARTED`, `HINT_EXTRACTION_FAILED`, `HINT_EXTRACTED`
- `STRATEGY_CARD_CREATED`, `STRATEGY_STEP_SELECTED`, `STRATEGY_STEP_UPDATED`
- `MANAGER_DECISION`, `ACTION_VALIDATION_FAILED`, `SEMANTIC_REPEAT_BLOCKED`
- `HTTP_SESSION_STATUS`, `CONTEXT_BUILT`, `ARTIFACT_RETRIEVED`
- `OBSERVER_TRIGGERED`, `OBSERVER_DIRECTIVE`, `OBSERVER_PATCH_APPLIED`
- `FLAG_CANDIDATE`, `FLAG_CONFIRMED`, `GATE_REJECTED`

Tool events distinguish proposed model intent, actual tool result, Observer advice
and host rejection. Event payloads contain only redacted arguments and safe Cookie
metadata. Existing clients may ignore unknown event types.

## 5. API compatibility

Existing v2 endpoints and fields remain readable. Session snapshots gain optional
`strategy_cards`, `artifact_indexes`, `http_sessions`, `observer` and
`context_metrics` projections. `GET /tasks/{id}/report` becomes a pure response and
never writes a file. Explicit audited export uses
`POST /tasks/{id}/report/export`. Artifact preview remains bounded and redacted;
structured retrieval is an additional GET query, not implicit raw download.

Web handles both historical snapshots without new fields and current governed
snapshots. It never fabricates strategy, HTTP state or Observer data.

## 6. Phased acceptance

1. **Contracts and persistence**: additive migrations open old databases; new
   models round-trip; event payloads and action governance remain compatible.
2. **Hint and Artifact flow**: scoped large HTML is saved raw, readable content is
   segmented, and a provenance-backed candidate card is available before Solver.
3. **HTTP flow**: same task+solver+origin keeps cookies; every isolation boundary
   is tested; secrets never reach events/reports/UI; restart degradation is clear.
4. **Governed native loop**: every action has a strategy step, rationale, expected
   evidence and risk; repeats need a reason/new evidence; Observer patches cannot
   execute or complete.
5. **Completion/API**: native and legacy use the same placeholder/format/content/
   ownership gate; report GET is side-effect free and export is explicit.
6. **Web/evals**: real strategy, Manager/Solver/Observer timeline, safe HTTP state,
   deviation/repeat/context warnings and required efficiency metrics render.
7. **Regression**: Python tests, frontend tests/build and a fully local mock flow
   prove article -> strategy -> form preflight -> Cookie continuity -> Artifact
   Flag confirmation. No real external target is used.

## 7. Risk controls

- Untrusted article text is data, never a system instruction. Extraction failure
  is visible and cannot masquerade as body content.
- External URLs require task scope authorization before fetch or Solver use.
- Strategy cards describe candidates, not verified vulnerabilities.
- Persistent-target changes require explicit side-effect and lower-impact
  alternative analysis; authorized CTF execution remains possible and audited.
- Context compaction never deletes or rewrites audit messages/tool pairs. It builds
  a separate provider view with Artifact references and retrievable segments.
- Cookie recovery across process restart is intentionally unsupported until a
  dedicated encrypted secret store exists; the runtime rebuilds safely and emits
  the degradation instead of placing secrets in a normal checkpoint.

## 8. Baseline and target metrics

The historical task is evidence only and was not re-run or modified. Its measured
baseline was 42 model turns, 48 actions, about 717.45 seconds inside model turns,
about 21.61 seconds inside actions, and roughly 475,050 serialized input characters
before the final turn. Five article-related tool messages alone carried 143,779
content characters. It had no StrategyCard or native Observer intervention.

| Metric | Historical baseline | Governed target |
| --- | ---: | ---: |
| Hint referenced by structured strategy | 0% | 100% for successfully extracted hints |
| Hint-to-first StrategyCard | not available | before Solver turn 1 |
| Exploit HTTP path after usable strategy | 48 total actions / long detour | 3 requests for the local article fixture |
| Semantic duplicate action rate | visible repeated downloads/inspection/payload repairs | 0 without an explicit retry reason |
| Working-context size | ~475,050 chars late in run | bounded projection; raw audit retained separately |
| Flag provenance completeness | native shortcut could accept weak evidence | 100% task-owned Artifact-backed confirmations |
| Cross-origin/session Cookie leak | no persistent profile to measure | 0; task+solver+origin isolation |
| High-side-effect actions without alternative analysis | present | 0 admitted by Manager |

The evaluation result schema now reports hint utilization, hint-to-strategy and
hint-to-flag turns/actions/wall time, duplicate rate, consecutive failures without
a new hypothesis, context size and Artifact retrievals, Observer adoption/invalid
interruption, flag provenance completeness and unaudited persistent state changes.
Legacy W1-W6 fixtures legitimately report zero hint/context/Observer observations;
the governed local mock regression supplies the non-legacy path acceptance.
