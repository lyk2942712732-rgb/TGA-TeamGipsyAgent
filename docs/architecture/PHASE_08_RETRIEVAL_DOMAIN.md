# Phase 8: retrieval domain and governed context

Phase 8 replaces the null RAG seam with a scope-neutral retrieval domain,
durable SQLite projections, frozen index snapshots, keyword retrieval, and a
governed path into Solver context. Retrieval remains source material: it does
not grant tools, activate a Skill, confirm an EvidenceClaim, or create verified
Knowledge by itself.

## Ownership and durable resources

`OwnerScope` supports four principals without making a Task the universal
container:

| Scope | Required identity | Valid examples |
| --- | --- | --- |
| `global` | none | built-in methods and shared references |
| `workspace` | `workspace_id` | organization or project corpora |
| `task` | `task_id` | Task Artifact and Task-local references |
| `solver` | `task_id`, `solver_id` | private Solver working material |

`KnowledgeBase`, `CorpusSource`, `CorpusDocument`, `DocumentRevision`,
`DocumentChunk`, `IndexSnapshot`, `IndexBinding`, `RetrievalRun`, and
`RetrievalHit` carry explicit ownership. Their SQLite tables use nullable owner
columns and deliberately have no mandatory `tasks` foreign key. A source also
records its `channel`, `kind`, and `trust_level`.

Documents are stable identities. Ingestion appends immutable revisions and
chunks, then advances the document's current-revision projection with CAS.
Creating a snapshot freezes the selected current revisions and chunk IDs;
later document revisions cannot mutate that snapshot.

`IndexBinding` pins a principal and purpose, such as a Task's `context`, to one
snapshot. Recovery reuses that binding. Refreshing it is an explicit operation,
not a side effect of reading context.

## Retrieval channels and policy

The service separates three channels:

- `skill`: method candidates, labelled as not active;
- `reference`: background material, labelled as not Task evidence;
- `task_artifact`: immutable Task outputs, labelled as candidate evidence.

`RetrievalPolicy` filters Knowledge Bases, source kinds, trust levels, owner
scopes, Task Artifact visibility, and cross-Solver visibility. The service
applies structural owner checks in addition to caller-provided filters. The
default policy excludes previous-Task history.

Keyword scoring is the required baseline and works without an embedding
provider. Vector scoring is optional and used only when the snapshot's
embedding model matches the configured gateway; otherwise retrieval falls back
to keyword scoring. One request may search multiple Knowledge Bases in a
single frozen snapshot.

Every request persists a `RetrievalRun` and ranked `RetrievalHit` records,
including principal, intent, original and rewritten query, snapshot, filters,
method, timestamps, scores, and selected/truncated state.

## Parsing, safety, and context

The parser creates type-aware locators for Markdown headings, code symbols,
log line ranges, JSON paths, HTTP request/response parts, PDF pages, and
explicitly extracted binary text. Parse failure is persisted on the document
revision. Suspected prompt injection is marked on chunks and all retrieved
content is wrapped and labelled as untrusted data.

`ContextBuilder` retrieves against the persisted `context` binding and applies
strict result and token budgets. It injects only bounded Reference and Task
Artifact sections; Skill retrieval is kept in the Skill-selection channel.
Retrieval failures degrade to an empty section and are counted in context
metrics instead of breaking the Solver loop.

Tool-initiated searches pass through `ToolGovernanceGateway` as
`retrieval.search`, so authorization, lifecycle, budget, observation, and audit
rules remain identical to other model-initiated actions. Retrieval tool output
is explicitly non-verified.

Successful Artifact-producing actions are projected into a Task Artifact
Knowledge Base. Projection is best-effort and cannot rewrite the authoritative
tool result. Each successful projection creates or reuses a frozen snapshot,
updates the Task context binding with CAS, and emits indexing/snapshot events.

## Evidence boundary

A Task Artifact hit may be converted to a candidate `EvidenceClaim` with the
original chunk locator. It cannot directly create a confirmed claim or
verified `KnowledgeItem`. Repository validation rejects retrieval-only
provenance for verified Knowledge unless a confirmed EvidenceClaim or an
explicit human source is present.

## Stable seams for Phase 08A

The following concepts are intentional extension seams, not Task-specific
implementation details:

- `KnowledgeBase` may aggregate multiple `CorpusSource` records;
- a `CorpusSource` keeps kind, trust, channel, owner, and arbitrary source
  metadata;
- a `CorpusDocument` has append-only `DocumentRevision` history;
- all resources retain `global`, `workspace`, `task`, or `solver` ownership;
- `IndexSnapshot` freezes one or more Knowledge Bases;
- `RetrievalRequest` and `RetrievalRun` support multi-KnowledgeBase retrieval;
- `DocumentParser`, `EmbeddingGateway`, `IndexRepository`, and
  `RetrievalGateway` remain application ports.

Phase 08A can add connectors, richer parsers, vector indexes, rerankers, and
trust workflows behind these seams without making `task_id` mandatory or
changing the candidate-evidence semantics.
