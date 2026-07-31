# Phase 2 legacy-domain migration matrix

| Legacy source | New destination | Fidelity | Verification treatment | Persistence dependency |
| --- | --- | --- | --- | --- |
| `TGATask.goal` and creation directives | `TaskSpec` / `TaskDirective` | Requires creation-input projection; no automatic converter yet | Formal directives remain authoritative, hints stay separate | Schema v6 task-spec storage in phase 4 |
| `MemoryEntry(kind="hint")` | `TaskHint` | Content/source/timestamps preserved | Always imports as `unreviewed` | Hint repository in phase 4 |
| `MemoryEntry(kind=fact/constraint/decision/failure_boundary)` | `KnowledgeItem` | Content and supersedes link preserved; legacy artifact IDs retained only in provenance | Always imports as `candidate`; artifact IDs are not relabelled as claims | Knowledge tables in phase 4 |
| `MemoryEntry(kind="evidence")` | No automatic Knowledge conversion | Ambiguous legacy semantics | Must be reviewed and converted through Artifact/EvidenceClaim | Manual migration policy plus phase 4 storage |
| `StrategyCard` | candidate `GlobalPlan` plus `LocalPlan` | Steps and provenance preserved; mixed claims/results remain provenance | Plans import as `draft`, intent as `proposed`, steps as `pending` | Global/local plan tables and CAS versioning in phase 4 |
| `ArtifactRecord` | immutable `Artifact` | Lossless for schema-v5 fields | No verification state is inferred | Artifact projection in phase 4 |
| legacy `Finding.evidence_artifact_id` | `EvidenceClaim` plus new `Finding` | Requires `legacy_whole_artifact` locator because offsets were not persisted | Claim and Finding always import as `candidate`, even if legacy status was confirmed | EvidenceClaim/Finding tables in phase 4 |
| legacy Finding without artifact ID | candidate new `Finding` without claim | Conclusion text preserved but unsupported | Cannot be confirmed automatically | Review workflow and phase 4 storage |

All converters in `tga/migrations/converters.py` are pure and add
`legacy_import=True` plus provenance. They do not read or write `EvidenceStore`.
