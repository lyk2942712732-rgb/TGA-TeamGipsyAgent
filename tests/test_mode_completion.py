from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tga.contracts import TGATask
from tga.capabilities.registry import build_default_registry
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.modes import MODE_PROFILES, TASK_MODES
from tga.runtime.completion_validators import (
    CompletionValidationContext,
    FinishSubmission,
    finish_tool_schema,
    validator_for,
)
from tga.runtime.coordinator import SessionCoordinator
from tga.runtime.prompts import build_agent_system_prompt
from tga.capabilities.registry import build_default_registry
from tga.skills.selection import SkillSelectionRequest, SkillSelector
from tga.tools.mcp_config import MCPVisibilityConfig, load_mcp_config


def _task(task_id: str, mode: str, **updates) -> TGATask:
    payload = {
        "id": task_id,
        "name": task_id,
        "mode": mode,
        "task_entry_url": "https://target.example",
        "goal": "complete the requested analysis",
        "flag_format": r"CTF\{[^}]+\}" if mode == "ctf" else None,
    }
    payload.update(updates)
    return TGATask.model_validate(payload)


def _context(tmp_path, task: TGATask, *, text: str = "evidence"):
    root = tmp_path / task.id
    store = EvidenceStore(root / "evidence.db")
    store.create_task(task)
    coordinator = SessionCoordinator(store)
    _, agent_id = coordinator.ensure_runtime(task=task, max_turns=4)
    coordinator.start(task_id=task.id, solver_id=agent_id)
    artifacts = ArtifactStore(root / "artifacts")
    artifact = artifacts.save_text(
        task_id=task.id, intent_id="act_1", kind="tool_output", text=text,
        tool="test.evidence", target=task.default_action_target(),
    )
    store.add_artifact(artifact)
    context = CompletionValidationContext(
        task=task,
        solver_id="solver_test",
        store=store,
        artifact_text=lambda _task_id, record: artifacts.read_text(record.id),
    )
    return store, artifact, context


def test_five_modes_are_authoritative_and_removed_aliases_are_rejected():
    assert set(TASK_MODES) == {
        "ctf", "penetration_test", "incident_response",
        "vulnerability_research", "reverse_engineering",
    }
    assert set(MODE_PROFILES) == set(TASK_MODES)
    for removed in ("web_audit", "code_audit", "binary_ctf"):
        with pytest.raises(ValueError, match="unsupported task mode"):
            _task(f"removed_{removed}", removed)
    for mode in TASK_MODES:
        assert _task(f"task_{mode}", mode).model_dump(mode="json")["mode"] == mode


def test_mode_registry_matches_mcp_defaults_persisted_config_and_frontend_contract():
    expected = tuple(TASK_MODES)
    assert tuple(MCPVisibilityConfig().modes) == expected

    project_root = Path(__file__).parents[1]
    config, _ = load_mcp_config(project_root / "config" / "mcp.json")
    for server in config.servers.values():
        assert set(server.visibility.modes) <= set(expected)
        for method in server.methods.values():
            if method.modes is not None:
                assert set(method.modes) <= set(expected)

    frontend_source = (project_root / "apps" / "web" / "src" / "modes.ts").read_text(encoding="utf-8")
    match = re.search(r"export const TASK_MODES = (\[[^;]+\]) as const", frontend_source)
    assert match is not None, "frontend TASK_MODES declaration is missing"
    assert tuple(json.loads(match.group(1))) == expected


def test_finish_schema_is_strict_and_exposes_flag_only_for_ctf():
    assert finish_tool_schema("ctf")["additionalProperties"] is False
    assert "flag" in finish_tool_schema("ctf")["properties"]
    for mode in TASK_MODES[1:]:
        schema = finish_tool_schema(mode)
        assert schema["additionalProperties"] is False
        assert "flag" not in schema["properties"]


def test_each_mode_drives_prompt_capabilities_and_skills():
    capabilities = build_default_registry().snapshot()["capabilities"]
    capabilities = build_default_registry().snapshot()["capabilities"]
    for mode in TASK_MODES:
        task = _task(f"profile_{mode}", mode)
        prompt = build_agent_system_prompt(task)
        assert MODE_PROFILES[mode].label in prompt
        assert MODE_PROFILES[mode].completion_focus in prompt
        assert any(mode in item["modes"] for item in capabilities)
        available = tuple(item["name"] for item in capabilities if mode in item["modes"])
        selection = SkillSelector().select(SkillSelectionRequest(
            mode=mode,
            goal=task.goal,
            prompt=task.session_input.prompt,
            mode_config=task.mode_config.model_dump(mode="json") if task.mode_config else {},
            available_capabilities=available,
        ))
        assert selection.selector.startswith("task-skill-selector-v1:")


def test_ctf_requires_an_artifact_backed_non_placeholder_flag(tmp_path):
    task = _task("ctf_gate", "ctf")
    store, artifact, context = _context(tmp_path, task, text="result CTF{real_evidence}")
    validator = validator_for(task.mode)

    missing = validator.validate(context=context, submission=FinishSubmission(summary="done"))
    fake = validator.validate(context=context, submission=FinishSubmission(
        summary="done", flag="CTF{real_evidence}", evidence_artifact_ids=["artifact_fabricated"],
    ))
    accepted = validator.validate(context=context, submission=FinishSubmission(
        summary="done", flag="CTF{real_evidence}", evidence_artifact_ids=[artifact.id],
    ))

    assert missing.accepted is False and missing.code == "CTF_FLAG_REQUIRED"
    assert fake.accepted is False and fake.code == "INVALID_EVIDENCE_REFERENCE"
    assert accepted.accepted is True and accepted.code == "CTF_FLAG_VERIFIED"
    assert accepted.evidence_artifact_ids == [artifact.id]
    assert store.task_snapshot(task.id)["flags"] == [{
        "value": "CTF{real_evidence}",
        "evidence_artifact_id": artifact.id,
        "created_at": store.task_snapshot(task.id)["flags"][0]["created_at"],
    }]
    store.close()


def test_configured_remote_flag_verifier_is_the_final_ctf_oracle(tmp_path):
    task = _task("ctf_remote", "ctf")
    store, artifact, context = _context(tmp_path, task, text="result CTF{remote_result}")
    submission = FinishSubmission(
        summary="done", flag="CTF{remote_result}", evidence_artifact_ids=[artifact.id],
    )
    context.remote_flag_verifier = lambda _task, _flag: False
    rejected = validator_for(task.mode).validate(context=context, submission=submission)
    assert rejected.code == "CTF_REMOTE_FLAG_REJECTED"
    assert store.task_snapshot(task.id)["flags"] == []

    context.remote_flag_verifier = lambda _task, _flag: True
    accepted = validator_for(task.mode).validate(context=context, submission=submission)
    assert accepted.accepted is True
    assert accepted.details["verification"] == "remote_verifier_accepted"
    assert any(event.type == "FLAG_CONFIRMED" for event in store.list_agent_events(task.id))
    store.close()


def test_finish_rejects_an_artifact_owned_by_another_task(tmp_path):
    task = _task("owner_a", "penetration_test")
    store, _, context = _context(tmp_path, task)
    foreign = ArtifactStore(tmp_path / "foreign" / "artifacts").save_text(
        task_id="owner_b", intent_id=None, kind="tool_output", text="foreign evidence",
    )
    store.add_artifact(foreign)
    result = validator_for(task.mode).validate(
        context=context,
        submission=FinishSubmission(
            summary="tested", evidence_artifact_ids=[foreign.id],
            coverage=["authorized surface"], limitations=["one environment"],
        ),
    )
    assert result.accepted is False
    assert result.code == "INVALID_EVIDENCE_REFERENCE"
    store.close()


def test_penetration_test_can_complete_with_evidence_and_no_findings(tmp_path):
    task = _task("pen_negative", "penetration_test")
    store, artifact, context = _context(tmp_path, task, text="requests and response comparison")
    result = validator_for(task.mode).validate(
        context=context,
        submission=FinishSubmission(
            summary="No vulnerability was confirmed.",
            evidence_artifact_ids=[artifact.id],
            coverage=["public routes", "authorization checks"],
            limitations=["no authenticated account"],
        ),
    )
    assert result.accepted is True
    assert not store.task_snapshot(task.id)["flags"]
    store.close()


def test_non_ctf_validator_does_not_accept_a_flag_field(tmp_path):
    task = _task("pen_flag", "penetration_test")
    store, artifact, context = _context(tmp_path, task)
    result = validator_for(task.mode).validate(
        context=context,
        submission=FinishSubmission(
            summary="tested", flag="CTF{irrelevant}", evidence_artifact_ids=[artifact.id],
            coverage=["surface"], limitations=["fixture"],
        ),
    )
    assert result.accepted is False and result.code == "FLAG_NOT_ALLOWED_FOR_MODE"
    store.close()


@pytest.mark.parametrize(
    ("mode", "claims", "expected_missing"),
    [
        (
            "vulnerability_research",
            [{"kind": "vulnerability", "statement": "input causes memory corruption"}],
            "reproduction Artifact",
        ),
        (
            "incident_response",
            [{"kind": "ioc", "statement": "10.0.0.8 is malicious"}],
            "evidence for IOC",
        ),
    ],
)
def test_key_security_claims_without_claim_evidence_are_rejected(tmp_path, mode, claims, expected_missing):
    task = _task(f"unsupported_{mode}", mode)
    store, artifact, context = _context(tmp_path, task)
    result = validator_for(task.mode).validate(
        context=context,
        submission=FinishSubmission(
            summary="conclusion", evidence_artifact_ids=[artifact.id],
            coverage=["relevant inputs"], limitations=["bounded fixture"], claims=claims,
        ),
    )
    assert result.accepted is False
    assert any(expected_missing in item for item in result.missing)
    store.close()


def test_reverse_engineering_without_analysis_artifact_is_rejected(tmp_path):
    task = _task("reverse_missing", "reverse_engineering")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    context = CompletionValidationContext(
        task=task, solver_id="solver_test", store=store,
        artifact_text=lambda *_: "",
    )
    result = validator_for(task.mode).validate(
        context=context,
        submission=FinishSubmission(
            summary="recovered algorithm", coverage=["entry function"],
            claims=[{"kind": "recovered_result", "statement": "algorithm recovered"}],
        ),
    )
    assert result.accepted is False
    assert "at least one task-owned evidence Artifact" in result.missing
    store.close()
