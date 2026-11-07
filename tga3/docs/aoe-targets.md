# AOE target lifecycle and card catalog

Scout records each question's target requirements in its `target` field. The contract is saved in the
question's `question.json` and the parent's `questions.json`; lifecycle requests are not added to the
frontend question overview. Modes:

- `none`: source data/collected attachments suffice; no target instance is required.
- `static`: an existing address is used without platform lifecycle calls.
- `managed`: the system acquires and releases a platform instance before/after an attempt.
- `unknown`: source information is insufficient. Preserve existing Worker handling; never assume offline.

For example, a platform-managed question can register:

```json
{
  "source_key": "g-01",
  "title": "Example question",
  "description": "Original statement",
  "target": {
    "mode": "managed",
    "start": {
      "method": "POST",
      "url": "https://platform.example/openapi/v1/challenges/start",
      "headers": {"BENCHMARK_TOKEN": "<actual user-supplied token>"},
      "params": {"unique_code": "g-01"}
    },
    "stop": {
      "method": "POST",
      "url": "https://platform.example/openapi/v1/challenges/close",
      "headers": {"BENCHMARK_TOKEN": "<actual user-supplied token>"},
      "params": {"unique_code": "g-01"}
    },
    "addresses_path": "container_addr",
    "quota_limit": 3,
    "quota_error": "max active challenge instances reached"
  }
}
```

The token above is only a documentation placeholder. Scout must collect actual authorized values and
documented endpoints, not guess them. `body` supports JSON request bodies. An optional `status` HTTP
request supports asynchronous startup; `addresses_path` selects the response field using a dot path.
An empty pre-start address does not prove that a question is offline. A bootstrap server needed to
obtain an attachment is also not proof that solving the downloaded attachment requires a live target.

The runtime shares quota accounting by endpoint host, account headers and optional `quota_scope`.
When capacity is exhausted it first schedules explicitly `none` questions, retaining existing untouched/
difficulty ordering within that subset and collection-phase restrictions. No attempt or CLI is started
while its managed instance is pending. Once ready, the ordinary first-Worker-action budget still applies.
Joint attempts share one target lease. Solved, timed-out and cancelled attempts stop Workers before
releasing the lease. Failed closes keep occupying local capacity and retry after backoff, including after
a control-plane restart. The system never evicts an instance leased to another active attempt/task.

Lease state and cleanup contracts live in `data/tasks/<parent>/target-instances.json`, not a new SQL table.
The API client is reused. No shell commands or new model calls are used for lifecycle operations. Normal
service shutdown preserves active leases/deadlines for recovery; explicit task cancellation releases them.
Historical questions without target metadata retain `unknown` behavior; existing records are not rewritten.

Workers receive a complete task-scoped card catalog containing only `card_id` and `title`, without a
length cutoff or statement-based filtering. Initial titles go directly into CLI system instructions,
because native CLI hooks truncate large outputs before the model sees them. The hook acknowledges those
exact title fingerprints without duplicating their contents. New/renamed titles during execution arrive
at ordinary tool boundaries, with bounded batches and exact ACKs; a backlog continues even if the index
mtime stays unchanged. CLI resume refreshes the full system catalog. ACK means delivered, not adopted. Body and assets
remain on-demand at `/experience/cards/<card_id>.json` and the card's resource paths. Old catalog ACK state
is migrated by redelivering the complete compact catalog once. This feature does not change Python,
installed dependencies or native CLI command lookup.
