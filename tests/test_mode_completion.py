from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tga.contracts import ActionResult, TGATask
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
from tga.runtime.challenge_state import ChallengeStateMachine
from tga.runtime.manager import Manager, RuntimeLimits
from tga.runtime.prompts import build_agent_system_prompt
from tga.orchestrator.planner import plan_initial_intents
from tga.skills.registry import SkillRegistry
from tga.tools.mcp_config import MCPVisibilityConfig, load_mcp_config


def _task(task_id: str, mode: str, **updates) -> TGATask:
    payload = {
        "id": task_id,
        "name": task_id,
        "mode": mode,
        "target": "https://target.example",
        "goal": "complete the requested analysis",
        "flag_format": r"CTF\{[^}]+\}" if mode == "ctf" else None,
    }
    payload.update(updates)
    return TGATask.model_validate(payload)


def _context(tmp_path, task: TGATask, *, text: str = "evidence"):
    root = tmp_path / task.id
    store = EvidenceStore(root / "evidence.db")
    store.create_task(task)
    ChallengeStateMachine(store).activate(task, reason="test_started")
    artifacts = ArtifactStore(root / "artifacts")
    artifact = artifacts.save_text(
        task_id=task.id, intent_id="act_1", kind="tool_output", text=text,
        tool="test.evidence", target=task.target,
    )
    store.add_artifact(artifact)
    context = CompletionValidationContext(
        task=task,
        solver_id="solver_test",
        store=store,
        artifact_text=lambda _task_id, record: artifacts.read_text(record.id),
    )
    return store, artifact, context


def test_five_modes_and_legacy_values_share_one_authoritative_migration_boundary():
    assert set(TASK_MODES) == {
        "ctf", "penetration_test", "incident_response",
        "vulnerability_research", "reverse_engineering",
    }
    assert set(MODE_PROFILES) == set(TASK_MODES)
    assert _task("old_web", "web_audit", flag_format="flag").mode == "penetration_test"
    assert _task("old_code", "code_audit").mode == "vulnerability_research"
    assert _task("old_binary", "binary_ctf").mode == "reverse_engineering"
    assert _task("old_web_flag", "web_audit", flag_format=r"FLAG\{.*\}").flag_format is None
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


def test_each_mode_drives_prompt_plan_capabilities_and_skills(monkeypatch):
    monkeypatch.setattr("tga.agent.llm_planner.build_model_client_from_env", lambda: None)
    capabilities = build_default_registry().snapshot()["capabilities"]
    skills = SkillRegistry()
    first_goals: set[str] = set()
    for mode in TASK_MODES:
        task = _task(f"profile_{mode}", mode)
        prompt = build_agent_system_prompt(task)
        assert MODE_PROFILES[mode].label in prompt
        assert MODE_PROFILES[mode].completion_focus in prompt
        first_goals.add(plan_initial_intents(task)[0].goal)
        assert any(mode in item["modes"] for item in capabilities)
        assert skills.query(mode=mode, limit=3)
    assert len(first_goals) == len(TASK_MODES)


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
    assert store.task_snapshot(task.id)["flags"][0]["evidence_artifact_id"] == artifact.id
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
    confirmed = next(event for event in store.list_agent_events(task.id) if event.type == "FLAG_CONFIRMED")
    assert confirmed.payload["verification"] == "remote_verifier_accepted"
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


class _RejectedThenRecoveredModel:
    model = "finish-state-machine-test"

    def __init__(self):
        self.turn = 0

    def chat_tools(self, messages, *, tools, temperature=0.2):
        self.turn += 1
        if self.turn == 1:
            call = {"id": "finish_bad", "type": "function", "function": {
                "name": "finish_session", "arguments": json.dumps({"summary": "done", "flag": "CTF{loop_ok}"}),
            }}
        elif self.turn == 2:
            rejected = json.loads(next(item["content"] for item in reversed(messages) if item.get("tool_call_id") == "finish_bad"))
            assert rejected["accepted"] is False and rejected["terminal"] is False
            call = {"id": "get_evidence", "type": "function", "function": {
                "name": "tga_http_request", "arguments": json.dumps({"method": "GET", "path": "/"}),
            }}
        else:
            tool_result = json.loads(next(item["content"] for item in reversed(messages) if item.get("tool_call_id") == "get_evidence"))
            artifact_id = tool_result["artifacts"][-1]["artifact_id"]
            call = {"id": "finish_good", "type": "function", "function": {
                "name": "finish_session", "arguments": json.dumps({
                    "summary": "Recovered the verified flag.", "flag": "CTF{loop_ok}",
                    "evidence_artifact_ids": [artifact_id],
                }),
            }}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [call]}, "finish_reason": "tool_calls"}


class _FlagExecutor:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def execute(self, *, task, action, workspace):
        artifact = self.artifacts.save_text(
            task_id=task.id, intent_id=action.id, kind="http_response",
            text="response CTF{loop_ok}", tool=action.capability, target=task.target,
        )
        return ActionResult(
            action_id=action.id, task_id=task.id, solver_id=action.solver_id,
            status="succeeded", summary="captured response", artifact_ids=[artifact.id],
            candidate_flags=["CTF{loop_ok}"],
        )


def test_rejected_finish_returns_to_the_same_session_then_can_complete(tmp_path, monkeypatch):
    task = _task("finish_retry", "ctf")
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)
    model = _RejectedThenRecoveredModel()
    monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: model)
    manager = Manager(
        store=store, run_root=root,
        executor=_FlagExecutor(ArtifactStore(root / task.id / "artifacts")),
    )
    snapshot = manager.run_session(task.id)
    event_types = [event["type"] for event in snapshot["agent_events"]]
    assert snapshot["session"]["status"] == "completed"
    assert snapshot["session"]["turn_count"] == 3
    assert "FINISH_REJECTED" in event_types and "FINISH_ACCEPTED" in event_types
    assert event_types.index("FINISH_REJECTED") < event_types.index("TOOL_EXECUTION_START") < event_types.index("FINISH_ACCEPTED")
    store.close()


class _NaturalTurnModel:
    model = "natural-turn-test"

    def chat_tools(self, messages, *, tools, temperature=0.2):
        return {
            "message": {"role": "assistant", "content": "Maybe CTF{plain_text_only}, but I have no evidence."},
            "finish_reason": "stop",
        }


def test_plain_flag_text_continues_until_max_turns_not_fixed_two_turn_block(tmp_path, monkeypatch):
    task = _task("plain_turns", "ctf")
    root = tmp_path / "runs"
    store = EvidenceStore(root / task.id / "evidence.db")
    store.create_task(task)
    monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: _NaturalTurnModel())
    manager = Manager(store=store, run_root=root)
    manager.limits = RuntimeLimits(max_turns=3)
    snapshot = manager.run_session(task.id)
    events = snapshot["agent_events"]
    assert snapshot["session"]["status"] == "blocked"
    assert snapshot["session"]["stop_reason"] == "session_turn_limit"
    assert snapshot["session"]["turn_count"] == 3
    assert len([event for event in events if event["type"] == "CONTINUATION_TRIGGERED"]) == 3
    assert len([event for event in events if event["type"] == "AGENT_TURN_ENDED"]) == 3
    assert not snapshot["flags"]
    store.close()
