# Phase 3 solver and team resources

The nine built-in SolverDefinitions are immutable JSON resources split by
orchestration role. `SolverDefinitionRegistry.builtin()` validates their schema,
Skill references, Capability references, unique IDs, content hashes and exact
built-in set.

The five `resources/team_templates/*.yaml` files contain JSON, which is a strict
YAML 1.2 subset and therefore needs no additional parser dependency. The team
registry validates Definition roles and mode compatibility. Every phase-3 team
has `max_active_workers = 1`; limited parallelism remains disabled until phase 7.

Skill ownership is now:

```text
TaskCommonSkillSnapshot (normally 1–2)
+ SolverSkillSnapshot (at most 3)
```

Both freeze version, body and SHA-256. `SkillActivation` records guidance use
but has no tool or capability-grant field. The application selection service
requires every Skill capability to be present in both the runtime Capability
catalog and the independently supplied ToolPolicy snapshot.

The existing Markdown sources now live under `resources/skills`; the legacy
registry reads the same files, so management behavior is preserved without two
built-in source trees.

Schema-v5 `TGATask.skill_bundle_snapshot` remains unchanged. A pure compatibility
projection can preserve all three legacy entries in a legacy-import Task Common
snapshot; this does not move data or alter runtime prompts in phase 3.
