# TGA schema v6 release notes

## Highlights

- TaskOrchestrator coordinates durable Supervisor, Worker, Reviewer, and Reporter identities.
- Intent scheduling supports serial execution and at most two independent Workers.
- ToolGovernanceGateway is the only model-initiated execution boundary.
- Artifact, EvidenceClaim, Finding, and scoped Knowledge have separate authority semantics.
- The product exposes one schema-v6 Task Runtime and one formal route table.

## One-time Cutover

Schema v6 is the only application, API, persistence, and Runtime schema. Schema v5 is
rejected with a Migration Required response and is readable only by the explicit offline
migration tool. There is no online compatibility adapter, replay fallback, dual read, or
dual write. Old Session pages, routes, Runtime models, feature flags, completion aliases,
and tool-dispatch paths were physically deleted.

## Security and verification

Hint, Skill, and RAG cannot widen authorization. Workers cannot complete Tasks, Reporters
cannot confirm Findings, and high-impact Actions require one-time Approval. Verification
includes backend tests, frontend tests/build, browser E2E, route checks, repository-wide
Cutover searches, migration rollback tests, and five responsive viewport checks.
