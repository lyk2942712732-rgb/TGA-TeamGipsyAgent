# TGA schema v6 release notes

## Highlights

- TaskOrchestrator with durable Supervisor, Worker, Reviewer and Reporter.
- serial and bounded two-Worker Intent scheduling.
- scoped Knowledge and separate Artifact/EvidenceClaim/Finding semantics.
- governed tools with Approval queue, budgets, idempotency and resource locks.
- four-scope, multi-KnowledgeBase Retrieval.
- Task command frontend with five mode scenes and read-only Replay.

## Compatibility

Schema v6 is the default. Schema v5 remains read-only for Snapshot/Event replay.
The single-Agent frontend and legacy models are compatibility surfaces, not v6
authorities. Explicit migration is optional, offline, dry-run by default,
backup-first and idempotent.

## Security and verification

Hint, Skill and RAG cannot widen authorization. Worker completion is forbidden,
Reviewer/Reporter policy is role-scoped, high-impact Actions require one-time
Approval, remote MCP has no workspace mount, and public Events redact secrets.
Release verification uses offline fixtures and records actual commands/results
in `docs/performance/BASELINE.md` and the Phase-12 handoff.

## Verified release gate

- backend: 374 passed, 2 skipped;
- frontend unit/component: 94 passed across 26 files;
- browser E2E: 11 passed across all five modes and both desktop/mobile widths;
- production frontend build: passed;
- schema-v5 migration: dry-run non-mutation, apply/backup, rollback, CLI and
  idempotency tests passed.
