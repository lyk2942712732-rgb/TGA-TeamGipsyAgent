# TGA Report Schema

Reports are Markdown and generated only from `EvidenceStore.task_snapshot(task_id)`.
The reporting layer must not query SQLite directly and must not upgrade a
candidate finding to confirmed.

Snapshot keys:

- `task`
- `intents`
- `artifacts`
- `findings`
- `flags`
- `events`

## Task

The `task` object is a serialized `TGATask` from `tga.contracts`.

Required report fields:

- `id`
- `name`
- `mode`
- `target`
- `scope`
- `intensity`
- `allow_active_scan`
- `goal`
- `flag_format`

## Findings

Only findings with `status == "confirmed"` appear under `Confirmed Findings`.
All other findings must appear under `Unverified Leads` or candidate sections.

Expected fields:

- `id`
- `title`
- `target`
- `severity`
- `status`
- `evidence_artifact_id`
- `evidence_excerpt`
- `reproduction_steps`
- `remediation`
- `tool`

## Artifacts

The report uses artifact metadata for evidence references and `Tools Used`.

Expected fields:

- `id`
- `task_id`
- `intent_id`
- `kind`
- `path`
- `sha256`
- `tool`
- `target`
- `created_at`

## Flags

Flags are displayed only after they have passed the A-owned flag gate and have
an `evidence_artifact_id`.

Expected fields:

- `value`
- `evidence_artifact_id`
- `created_at`

## Events

Events explain leads, dead ends, tool failures, and other execution details.
The report currently recognizes `unverified_lead`, `lead`, `deadend`, and
`dead_end` event types.
