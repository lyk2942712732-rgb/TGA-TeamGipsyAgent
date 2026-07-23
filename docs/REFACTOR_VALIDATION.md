# AgentSession refactor validation

> Historical validation from 2026-07-17. The statements below about bypassing
> scope/action/evidence gates are superseded by the governed runtime implemented
> after `TASK_C3F31F2D6265_ANALYSIS.md`; see `ARCHITECTURE_IMPROVEMENT_PLAN.md`.

Validated on 2026-07-17 from the repository root.

## Schema-v4 Session update (2026-07-23)

The new-Session product path now uses staged task files, optional Hint text and
attachments, one persistent Session workspace, and automatic global MCP
capabilities. URL/reference/MCP Resource/Tool inputs and task-level MCP grants
were removed from both the frontend and API write path. Historical schema 2/3
Sessions remain readable.

Focused validation added for streamed upload metadata/MIME/SHA/limits, retry and
expiry cleanup, duplicate filenames, mode-specific empty input rules, schema-aware
artifact/input readers, SQLite migration, real image content blocks, text-only
image guidance, Docker/remote workspace status, automatic MCP snapshots, live
disable, new-service isolation, frontend reset/error/summary behavior, and a
Playwright file-plus-image wizard flow. The final complete command results are
recorded in the implementation report for this change.

## Delivered product path

1. `TaskRuntimeService` owns task lifecycle commands and queries.
2. `Manager` creates or resumes one persistent `AgentToolSession` for a normal
   configured-model task.
3. The model receives real tool definitions, emits native tool calls, and receives
   the matching tool results in the same transcript.
4. The Session persists messages and a private workspace, so pause/resume and process
   restart continue the same conversation.
5. Ordered message/tool/session events drive API and Web projections.

The old hypothesis fan-out and one-`ActionSpec` planning loop are not authorities
on the configured-model product path. General execution boundaries and
evidence/completion gates remain authoritative. Compatibility columns and an
explicitly injected legacy Solver remain readable for old runs and tests only.

## Failure reproduced and fixed

`task_fc7dbe693dfd` stopped before turn 1 because its provider rejected an unpaired
UTF-16 surrogate in `messages[1].content`. The provider adapter now normalizes every
string to Unicode scalar values before JSON serialization. A regression test sends
the malformed value through an OpenAI-compatible tool-call response.

## Commands executed

| Command | Result |
| --- | --- |
| `python -m compileall -q tga apps` | Passed |
| `pytest -q` | 136 passed; one upstream Starlette/httpx deprecation warning |
| `cd apps/web; npm test -- --run` | 9 test files, 21 tests passed |
| `cd apps/web; npm run build` | TypeScript and Vite build passed; 346 modules transformed |
| `cd apps/web; npm run test:e2e` | 5 Playwright tests passed, including 1280/1024/768 px runtime paths |
| `git diff --check` | Passed; only LF-to-CRLF checkout notices were reported |

The backend vertical test uses the real controlled executor against a local HTTP
server. It proves that the native Session can call `http.request`, store the result,
find the flag, and complete without New Session scope or active-scan switches.

The existing `task_fc7dbe693dfd` was then resumed against the configured
`deepseek-v4-pro` provider after restarting the local Web/API process with the new
runtime. It projected one Solver, completed 16 Agent turns and 16 tool actions,
stored 15 artifacts, emitted `FLAG_FOUND`, and stopped as `completed` with
`flag_observed`. This is the live-provider regression for the original failing run.

## Environment limit

Credentials are process-local and are never written into this validation file. A
server restart must inherit or re-enter the configured provider environment.
