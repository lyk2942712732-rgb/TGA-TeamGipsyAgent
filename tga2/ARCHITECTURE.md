# TGA2 architecture

```text
apps/web -> apps/api -> tga2/bootstrap.Container
                         |-> tga2/agent (LangChain + LangGraph)
                         |-> tga2/core (product truth)
                         |-> tga2/integrations (model + MCP)
```

LangChain owns agent tool loops, structured output, retries, HITL approval and
Docker shell execution middleware. LangGraph owns topology, checkpoints,
interrupt and resume. TGA2 owns authorization, redaction, Artifact hashes,
EvidenceClaim/Finding validation, business events and reports.

The task graph combines the orchestrator-worker and evaluator-optimizer
patterns:

```text
preflight -> initial_plan -> worker -> reviewer -> supervisor_checkpoint
                                   ^                    |
                                   |------ retry -------|
```

The checkpoint can also advance to the next Intent, append a bounded Plan
revision, interrupt for user input, or finish with a report. Supervisor,
Reviewer and Reporter use one bounded structured model decision; only Worker
owns a model/tool loop. Each Worker Attempt reserves the last two calls in its
role budget for a separate, tool-free finalizer. The investigation sub-agent
therefore cannot spend the entire budget repeatedly calling Kali, and tool-loop
markup such as DeepSeek DSML is never treated as the final `WorkerDraft`.

Every new Intent is an acceptance contract, not just a title and objective. The
Supervisor must provide numbered `success_criteria` and `expected_evidence`;
Runtime snapshots the policy-derived `allowed_tools` and adds immutable budget,
authorization and anti-repetition `stop_conditions`. After each tool result Runtime reminds Worker of the
checklist. Worker and Reviewer return index-based criterion assessments, and
Runtime rejects a `pass` that does not cover every criterion.

Intent transitions are Runtime-owned as well. Planned dependency indexes are
resolved into persisted Intent IDs and only ready Intents are dispatched. Before
each Worker starts, Runtime builds a bounded handoff ledger from TaskStore rather
than replaying prior chat. The ledger contains terminal Intent summaries,
confirmed evidence, findings, Artifact metadata and prior commands. Workers use
`list_artifacts` and `read_artifact` for selective retrieval. A cross-Intent
Artifact remains immutable; the consuming Intent creates its own Claim with both
consumer and source Intent IDs, preserving Reviewer isolation and provenance.

There is one application container. API, CLI and tests receive the same Runtime,
configuration, Skill repository and MCP repository. Settings pages therefore
change the real execution objects instead of maintaining a display-only copy.
