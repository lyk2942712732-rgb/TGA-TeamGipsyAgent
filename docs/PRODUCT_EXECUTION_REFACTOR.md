# Product Execution Refactor and Schema v5 Migration

## 1. Purpose and status

This document consolidates the product execution refactor requested in the supplied implementation brief and the explicit schema v4 to v5 migration delivered with it. It is the release contract for removing the split legacy target/input model and moving to one governed ReAct execution path.

Status labels used below:

- **Implemented and tested**: covered by the current automated backend and/or frontend suite.
- **Implemented, external validation pending**: code and protocol tests exist, but a real provider, isolated runner, or controlled network fixture is still required.
- **External**: requires infrastructure, credentials, authenticated identity, or an independently controlled test target.

The schema migration remains explicit-only. Application startup and all runtime read paths reject unsupported schema versions and never perform a migration.

## 2. Product execution contract

The target product has one execution chain:

```text
User/API -> TaskRuntimeService -> Manager -> SessionCoordinator
-> AgentSessionRunner -> ModelClient.chat_tools -> ToolDispatcher
-> governed handler -> ActionResult + Artifact + AgentEvent
-> tool message to the same ReAct transcript -> CompletionService
```

The schema v5 task boundary is:

```json
{
  "session_input": {
    "prompt": "initial user task input",
    "files": [{"kind": "task_input", "relative_path": "inputs/files/<stored-name>"}]
  },
  "task_entry_url": "https://challenge.example/path",
  "execution_policy": {
    "preset": "autonomous_ctf|safe_observation|offline_analysis|custom",
    "network": {},
    "local_compute": {},
    "high_impact": {}
  },
  "schema_version": 5
}
```

Execution boundaries remain independent:

1. `network.access` controls where network requests may go.
2. `network.interaction` controls observation versus ordinary interaction.
3. `local_compute` controls whether local code may run and requires isolation.
4. `high_impact` governs persistent or high-risk side effects.

`task_entry_url` is a display, initialization, and relative-URL base. Authorization comes from `network.seed_origins` or `network.custom_origins`, not from a second target/scope model.

## 3. Explicit offline migration

### 3.1 Preconditions

- Stop the API/runtime process for the task.
- Use a database containing exactly one schema v4 task and one matching schema v4 session.
- The session must not be `running` or `awaiting_approval`.
- Legacy files must still exist at their persisted `workspace/inputs/task/*` or `workspace/inputs/hints/*` paths and match persisted size and SHA-256.
- `workspace/inputs/files` must not already exist. This avoids merging into an unknown partial migration.
- Keep sufficient free space for one database backup, one temporary database, and one copied input set.

### 3.2 Command

Migration occurs only through this explicit command:

```powershell
python scripts/migrate_schema_v4_to_v5.py --db runs/<task-id>/evidence.db
```

Importing the module, opening a task, listing tasks, or starting the application does not invoke the migration.

### 3.3 Safety and publication order

The command performs these operations:

1. Opens the source database read-only and validates its task, session, status, and workspace.
2. Creates a SQLite-consistent unique backup named `evidence.db.v4-backup-<UTC timestamp>-<unique suffix>` and runs `PRAGMA integrity_check` on it.
3. Obtains an exclusive source lock and verifies that task/session data did not change while the backup was created.
4. Builds a temporary database from the backup and a hidden temporary `inputs/files` tree.
5. Verifies every source file before and after copy using size and SHA-256.
6. Transforms and validates the task with the current `TGATask` schema and validates the temporary database with `PRAGMA integrity_check`.
7. Publishes `workspace/inputs/files`, releases the read lock, and atomically replaces `evidence.db` with the validated temporary database.
8. On an exception, rolls back the database transaction, removes temporary/published v5 files, and leaves the original database and legacy files unchanged. The backup is intentionally retained.

The command emits only a stable error code or safe task/path/count/schema metadata. It never prints task JSON, prompt text, URL content, headers, cookies, credentials, or exception details from unknown failures.

### 3.4 Data mapping

| Schema v4 | Schema v5 | Rule |
|---|---|---|
| `session_input.hint.text` | `session_input.prompt` | Trim and preserve old hint text. |
| `taskFiles[]`, then `hint.files[]` | `session_input.files[]` | Preserve ordering and metadata; set every `kind` to `task_input`. |
| `inputs/task/*`, `inputs/hints/*` | `inputs/files/*` | Copy to a staged tree; verify persisted and copied hashes. Legacy files remain as rollback material. |
| URL in prompt | `task_entry_url` | Use the first valid credential-free absolute HTTP(S) URL. |
| old `target` URL | `task_entry_url` fallback | Consider after all prompt URLs. |
| all prompt/target URLs | `network.seed_origins` | Normalize default ports and deduplicate in encounter order. |
| `network.mode=none` | disabled/observe | No network access is inherited. |
| observe/interact plus `*` | public internet | Preserve interaction; retain all SSRF deny defaults. |
| observe/interact plus scopes | custom origins | Only safely normalized HTTP(S) origins are retained. Unsupported wildcard/CIDR syntax is rejected rather than broadened. |
| observe/interact with no scopes | task sources | Use extracted input origins; otherwise disable network. |
| `process_execution=forbidden` | local compute disabled | No execution permission. |
| `sandbox_only` | isolated | Preserve only the isolated intent. |
| `authorized_host` | isolated | Deliberately remove host execution authority; never broaden permissions. |
| state/containment authorized + allowlist | high impact allowlisted | Merge and deduplicate allowed actions. |
| state/containment approval required or fuzzing enabled | high impact approval required | Approval takes precedence over an allowlist. |
| all other high-impact combinations | forbidden | Least-privilege fallback. |

Rates, concurrency, and timeouts are clamped to schema v5 bounds. A preset is assigned only when the resulting dimensions exactly match that preset; all other combinations become `custom`.

### 3.5 Rollback

Automatic rollback applies before the command reports success. For an operator rollback after success:

1. Stop the runtime.
2. Verify the backup's `PRAGMA integrity_check` and schema v4 task/session.
3. Move the current schema v5 database aside for incident analysis.
4. Restore the retained `evidence.db.v4-backup-*` as `evidence.db` using an atomic same-volume rename.
5. Remove `workspace/inputs/files` only after confirming legacy `inputs/task` and `inputs/hints` are intact.

Do not open the restored v4 database through current runtime read paths; either migrate again explicitly or use a compatible v4 build.

## 4. Six implementation phases

| Phase | Scope from the supplied brief | State |
|---|---|---|
| 1. Contract | Define v5 `ExecutionPolicy`, remove target/targets/scope dependencies, unify multimodal input, update persistence/task creation | Implemented and tested. |
| 2. Network | URL seeds, HTTP target resolution, SSRF and redirect checks, real rate/concurrency enforcement, StrategyCard initialization from prompt | Implemented and tested with local policy fixtures; controlled external-target verification remains. |
| 3. Provider | Unified capability probe, remove auto+32-token false negative, reasoning truncation retry, validation states and frozen model snapshot | Implemented and tested with protocol fixtures; real-provider validation pending. |
| 4. Isolation | Real isolated local compute, remove `authorized_host`, fixed workspace permissions | Host execution is removed and isolated policy is enforced; Docker/container escape validation remains external. |
| 5. Approval | Persist pending ActionSpec, awaiting-approval lifecycle, approve/reject/timeout/cancel and audit events | Implemented and tested, including effect review card and durable timeout rejection. |
| 6. Product/UI | New Task, Provider, Runtime and Dashboard semantics; unit/integration/E2E and real-provider verification | Implemented and tested in browser fixtures; real-provider and infrastructure scenarios remain external. |

Each phase is a release gate. A later phase must not reintroduce legacy fallback to make an earlier phase appear complete.

## 5. Seventeen-bug remediation ledger

The following 17 product bugs consolidate the concrete failures called out by the supplied brief. They are behavioral defects, not style concerns.

| ID | Trigger and impact | Required fix | State |
|---|---|---|---|
| B01-B06 | Split inputs, implicit URL authorization, and SSRF/DNS/redirect enforcement gaps. | Unified v5 input, seed-origin policy, request-time DNS/redirect validation. | Implemented and tested with local fixtures; safe external target validation pending. |
| B07 | Ordinary POST and persistent side effects are conflated. | Separate interaction policy from a structured `ActionEffect`. | Implemented and tested. |
| B08 | `approval_required` rejects rather than queues executable work. | Persist original `ActionSpec`; approve, reject, expire, or cancel through the same lifecycle. | Implemented and tested. |
| B09-B10 | Host-process execution authority can leak into product policy. | Remove `authorized_host`; enforce disabled/isolated policy only. | Implemented; container escape proof remains external. |
| B11 | Limits can be bypassed or released incorrectly. | Atomic budget reservation and lifecycle release. | Implemented and tested. |
| B12-B15 | Provider probes and multimodal requests can produce false negatives or leak data. | Capability snapshots, forced/auto probe, bounded retry, write-only credential handling. | Implemented and protocol-tested; real-provider validation pending. |
| B16-B17 | Runtime/API/UI loses structured state or legacy target assumptions. | Structured lifecycle events, blocked recovery, entry/prompt summaries. | Implemented and browser-tested. |

## 6. Acceptance matrix

### 6.1 End-to-end scenarios

| Scenario | Acceptance evidence | Owner/dependency | Gate |
|---|---|---|---|
| A. Autonomous CTF | Prompt URL becomes entry URL and seed; StrategyCard cites it; verified model starts; real HTTP Action/Artifact/tool message occur; completion gate returns evidence-backed result without legacy target fields. | Backend + frontend; configured provider and safe challenge target. | Planned external E2E. |
| B. DeepSeek reasoning model | Forced and auto tool probes pass; reasoning truncation gets one larger-budget retry; real incompatibility is structured; turn zero is not irrecoverably failed. | Provider adapter; real configured DeepSeek-compatible endpoint. | Planned external integration. |
| C. High-impact approval | Proposed action becomes awaiting approval; UI shows redacted effect/risk; approval executes the same ActionSpec; rejection returns to same transcript; events are complete. | Runtime, API, frontend. | Planned E2E. |
| D. Isolated process | Python/shell reads inputs, writes work/artifacts, cannot read host paths or bypass network policy, and obeys timeout/output/PID limits. | Container/worker platform. | Planned infrastructure test. |

### 6.2 Automated verification matrix

| Layer | Required coverage | Current result |
|---|---|---|
| Migration | Explicit-only invocation, unique backup, prompt/URL mapping, file merge/hash, policy mapping, v5 validation, non-v4/active refusal, rollback, rerun refusal, no secret output | Covered by `tests/test_schema_v5_migration.py`; see latest test run in delivery report. |
| Network unit | URL normalization, task-source/public/custom authorization, private/DNS/redirect denial, observe/interact, no silent scope growth | Planned. |
| Approval/local compute | Pending ActionSpec, approve/reject continuity, disabled/isolated enforcement, workspace escape prevention | Planned. |
| Provider/model | stale state, endpoint normalization, key redaction, forced/auto tools, reasoning retry, blocked recovery, frozen snapshot | Planned; real-provider subset is external. |
| Runtime | prompt-based StrategyCard, rate/concurrency, structured error, lifecycle and recovery | Planned. |
| Frontend | preset/custom controls, no host execution, approval UI, provider states, structured errors, blocked recovery, no `task.target`, Chinese copy | Planned. |
| Integration | real HTTP/ReAct/artifacts/completion and no key leakage | Planned; requires safe target and configured provider. |
| E2E | desktop/mobile Runtime, event reconnect, scenarios A-D | Planned; requires running API/web and infrastructure. |

Release acceptance requires all applicable rows to be green. Unit fakes may validate protocol behavior, but cannot substitute for the real-provider and isolation gates.

### 6.3 Detailed acceptance checklist from the supplied brief

| ID | Layer | Required check | Gate |
|---|---|---|---|
| U01 | Unit | Extract initial-text URLs and normalize origins. | Migration covered; runtime unit planned. |
| U02 | Unit | `task_sources` permits only initial user origins. | Planned. |
| U03 | Unit | `public_internet` permits public URLs. | Planned. |
| U04 | Unit | Reject loopback, private, link-local, and cloud metadata addresses. | Planned. |
| U05 | Unit | Reject DNS results that resolve to private addresses. | Planned. |
| U06 | Unit | Reject redirects to private or unauthorized origins. | Planned. |
| U07 | Unit | `interact` permits ordinary POST. | Planned. |
| U08 | Unit | High-impact POST enters approval. | Planned. |
| U09 | Unit | `approval_required` persists a pending Action rather than failing it. | Planned. |
| U10 | Unit | Approval executes the original ActionSpec. | Planned. |
| U11 | Unit | Rejection returns a structured result to the same ReAct conversation. | Planned. |
| U12 | Unit | Disabled local compute rejects Python/shell. | Planned. |
| U13 | Unit | Isolated local compute uses the isolation executor. | Planned/external runner. |
| U14 | Unit | Workspace paths cannot escape to host paths. | Planned/external runner. |
| U15 | Unit | Rate and concurrency limits actually reserve and release. | Planned. |
| U16 | Unit | Initial prompt creates the StrategyCard. | Planned. |
| U17 | Unit | Runtime-added Hint cannot silently enlarge `task_sources`. | Planned. |
| U18 | Unit | Model configuration changes mark validation stale. | Planned. |
| U19 | Unit | Base URL does not duplicate `/chat/completions`. | Planned. |
| U20 | Unit | API key never appears in responses, logs, events, or Transcript. | Migration CLI covered; runtime/provider planned. |
| U21 | Unit | Reasoning token usage does not produce a tool-support false negative. | Planned. |
| U22 | Unit | Length finish with no tool call gets exactly one larger-budget retry. | Planned. |
| U23 | Unit | Provider request failure blocks rather than fails the Session. | Planned. |
| U24 | Unit | Runtime returns a structured error. | Planned. |
| U25 | Unit | Resume uses the frozen model snapshot. | Planned. |
| I01 | Integration | Real configured provider passes forced-tool validation. | External. |
| I02 | Integration | Real configured provider passes auto-tool validation. | External. |
| I03 | Integration | Real product tool catalog passes provider protocol validation without execution. | External. |
| I04 | Integration | Create a task whose target URL exists only in initial Hint. | Planned/external target. |
| I05 | Integration | Agent's first turn can call real `http.request`. | Planned/external target. |
| I06 | Integration | HTTP Action, Artifact, and ReAct timeline are produced. | Planned. |
| I07 | Integration | `finish_session` passes the completion gate and returns a result. | Planned. |
| I08 | Integration | API key is absent throughout the run and recorded outputs. | External secret scan. |
| I09 | Integration | External network tests access only explicit safe targets. | External QA fixtures. |
| I10 | Integration | Real failures retain redacted diagnostics and are never replaced with fake success. | External. |
| F01 | Frontend | Default view shows execution presets. | Planned. |
| F02 | Frontend | Custom mode expands network access, interaction, local compute, and high impact. | Planned. |
| F03 | Frontend | Ordinary users never see `authorized_host`. | Planned. |
| F04 | Frontend | No ambiguous single none/observe/interact network selector remains. | Planned. |
| F05 | Frontend | Approval card supports approve and reject. | Planned. |
| F06 | Frontend | Provider validation states are explicit. | Planned. |
| F07 | Frontend | Runtime displays structured errors. | Planned. |
| F08 | Frontend | Blocked Session can retry and resume. | Planned. |
| F09 | Frontend | Dashboard no longer depends on `task.target`. | Planned. |
| F10 | Frontend | Primary product guidance is in clear Chinese. | Planned. |

## 7. External dependencies and ownership

| Dependency | Why it is external | Required evidence / owner |
|---|---|---|
| Real OpenAI-compatible/DeepSeek provider | Tool, reasoning, visual, token-budget, and endpoint behavior cannot be proven by a fake. | Provider owner supplies write-only credential and test model; CI stores only redacted result/fingerprint. |
| Credential storage | Windows plaintext environment/config is not sufficient for product persistence. | Platform owner provides DPAPI or equivalent credential reference; no key in task DB/events/transcript. |
| Isolated worker/container runtime | Filesystem, PID, capability, network, timeout, and output limits require an actual isolation boundary. | Infrastructure owner supplies rootless/non-privileged runtime, fixed mounts, no Docker socket, governed egress. |
| Safe network targets | SSRF/redirect/DNS and CTF E2E must not probe arbitrary systems. | Security/QA owns dedicated public, redirect, private-resolution, and challenge fixtures. |
| Approval UX/session identity | Approval requires an authenticated decision maker and durable correlation. | API/frontend/auth owners define approver identity, timeout, cancellation, and audit retention. |
| DNS and egress enforcement | Application URL checks alone cannot prevent all container-side bypasses. | Network/platform owner enforces governed proxy/egress and provides DNS-rebinding tests. |
| Browser/E2E environment | Desktop/mobile layout, SSE reconnect, and approval interactions require running services and browsers. | Frontend QA owns Playwright environment and sanitized artifacts. |

## 8. Release gates and observability

Before enabling schema v5 execution in production:

1. Migrate a copied production-shaped v4 task and verify task/session schema 5, file hashes, entry URL, origins, and policy.
2. Prove rollback using the retained unique backup.
3. Run backend, frontend, type, lint, build, Playwright desktop/mobile, and secret scans.
4. Run real-provider forced tool, auto tool, real catalog, and reasoning-truncation tests without logging response bodies or credentials.
5. Run safe-target SSRF, redirect, rate, concurrency, and ReAct completion tests.
6. Run container escape, host-path, egress bypass, timeout, output, and PID-limit tests.
7. Confirm stable events for input analysis, network seeds, provider validation/retry, messages, actions, approval, limits, tools, block/resume, completion, and stop.
8. Reject release if any runtime read path mutates schema, any legacy authorization fallback remains, or any sensitive value appears in source, logs, events, transcripts, artifacts, screenshots, or test output.

The migration backup is not a long-term compatibility layer. After the retention window and verified rollback drill, archive or delete it under the product's data-retention policy rather than loading it through schema v5 runtime code.
