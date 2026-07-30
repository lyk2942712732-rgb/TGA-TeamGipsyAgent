# Retrieval and RAG

Retrieval is an independent domain, not Memory and not a Task-owned helper.
`KnowledgeBase`, `CorpusSource`, `CorpusDocument`, `DocumentRevision`,
`IndexSnapshot` and `RetrievalRun` use an explicit OwnerScope:

```text
global
workspace
task
solver
```

`task_id` is required only for scopes that need it. Global/workspace records
remain valid. A frozen IndexSnapshot may select multiple KnowledgeBase values
and an IndexBinding pins recovery to that exact selection.

Sources carry channel, kind, TrustLevel, owner, revision and provenance.
Document Revision values are append-only; chunks preserve locators and parser
safety flags such as prompt injection. RetrievalPolicy intersects visibility,
trust, channels, sources, result count and context budget. Retrieved text cannot
widen it.

RetrievedContextPack is bounded, labelled reference material. A Task Artifact
hit may create only a candidate EvidenceClaim. Retrieval cannot directly
confirm a Claim/Finding or create verified Knowledge.

Phase 8 intentionally omitted a complete global knowledge-management UI. The
domain, Repository and Gateway retain KnowledgeBase, CorpusSource, Document
Revision, TrustLevel, Owner/Scope, IndexSnapshot and multi-KnowledgeBase
retrieval seams for a later platform.
