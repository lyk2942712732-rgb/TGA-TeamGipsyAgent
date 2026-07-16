"""Executable, isolated W1-W6 evaluations for the durable v2 runtime."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from challenges import ChallengeFixture, build_fixture
from tga.capabilities.runtime import ControlledActionExecutor
from tga.contracts import ActionResult, ActionSpec, Hypothesis, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.runtime.board import HypothesisDraft
from tga.runtime.manager import Manager


ActionBuilder = Callable[[TGATask, str, str, dict[str, Any]], list[dict[str, Any]]]


@dataclass
class CaseRun:
    fixture: ChallengeFixture
    snapshot: dict[str, Any]
    run_root: Path


class ScriptedCaseSolver:
    """A deterministic driver that still crosses every production boundary."""

    model_name = "eval-scripted-solver"

    def __init__(self, builder: ActionBuilder, attack_class: str) -> None:
        self.builder = builder
        self.attack_class = attack_class

    def initial_hypotheses(self, *, task: TGATask, solver_id: str) -> list[HypothesisDraft]:
        return [
            HypothesisDraft(
                statement=f"The {self.attack_class} challenge has a bounded, artifact-producing proof path.",
                attack_class=self.attack_class,
                entry_point=task.target,
                rationale="Isolated challenge contract with explicit scope and capability budget.",
                next_test="Execute the next contract-derived action and retain its artifact.",
                confidence=0.9,
            )
        ]

    def propose_action(
        self, *, task: TGATask, solver_id: str, hypothesis: Hypothesis, snapshot: dict[str, Any]
    ) -> ActionSpec | None:
        actions = self.builder(task, solver_id, hypothesis.id, snapshot)
        index = len(snapshot.get("recent_actions") or [])
        if index >= len(actions):
            return None
        spec = actions[index]
        return ActionSpec(
            id=f"eval_{uuid4().hex[:12]}",
            task_id=task.id,
            solver_id=solver_id,
            hypothesis_id=hypothesis.id,
            capability=spec["capability"],
            kind=spec["kind"],
            target=spec.get("target", task.target),
            arguments=spec["arguments"],
            rationale=spec.get("rationale", "Execute one bounded evaluation action."),
            risk=spec.get("risk", "passive"),
        )

    def result_summary(self, *, hypothesis: Hypothesis, result: ActionResult) -> str:
        return result.summary


class FixtureExecutor:
    def __init__(self, fixture: ChallengeFixture, delegate: ControlledActionExecutor) -> None:
        self.fixture = fixture
        self.delegate = delegate

    def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult:
        if self.fixture.contract.case_id == "W5":
            workspace.mkdir(parents=True, exist_ok=True)
            attachment = workspace / "source_attachment.py"
            if not attachment.exists():
                attachment.write_text(
                    "# bundled debug constant left in an attachment\n"
                    f"DEBUG_PROOF = {self.fixture.oracle.expected_flag!r}\n",
                    encoding="utf-8",
                )
        return self.delegate.execute(task=task, action=action, workspace=workspace)


def _http(path: str, *, method: str = "GET", body: dict[str, str] | None = None, headers: dict[str, str] | None = None, risk: str = "passive") -> dict[str, Any]:
    arguments: dict[str, Any] = {"method": method, "path": path}
    if body is not None:
        arguments["body"] = body
        arguments["headers"] = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
    elif headers:
        arguments["headers"] = headers
    return {"capability": "http.request", "kind": "http", "arguments": arguments, "risk": risk}


def _actions_for(fixture: ChallengeFixture) -> ActionBuilder:
    case_id = fixture.contract.case_id

    def build(task: TGATask, solver_id: str, hypothesis_id: str, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        if case_id == "W1":
            return [_http("/robots.txt"), _http("/internal-proof")]
        if case_id == "W2":
            return [
                _http("/"),
                _http("/session", method="POST", body={"account": "auditor", "access_code": "open-sesame"}, risk="active"),
            ]
        if case_id == "W3":
            signature = hashlib.sha256(b"eval-nonce:tga-eval").hexdigest()
            return [
                _http("/signing-info"),
                {
                    "capability": "workspace.python",
                    "kind": "workspace",
                    "target": "solver workspace",
                    "arguments": {"source": "import hashlib\nprint(hashlib.sha256(b'eval-nonce:tga-eval').hexdigest())\n", "argv": []},
                    "risk": "active",
                },
                _http("/signed-proof", headers={"X-Challenge-Signature": signature}),
            ]
        if case_id == "W4":
            return [_http("/api/records/100"), _http("/api/records/101")]
        if case_id == "W5":
            return [
                {
                    "capability": "workspace.read",
                    "kind": "workspace",
                    "target": "source_attachment.py",
                    "arguments": {"relative_path": "source_attachment.py"},
                }
            ]
        if case_id == "W6":
            encoded = list(fixture.oracle.expected_flag.encode("utf-8"))
            source = f"encoded = {encoded!r}\nprint(bytes(encoded).decode('utf-8'))\n"
            return [
                {
                    "capability": "workspace.python",
                    "kind": "workspace",
                    "target": "solver workspace",
                    "arguments": {"source": source, "argv": []},
                    "risk": "active",
                }
            ]
        raise ValueError(f"unknown evaluation case: {case_id}")

    return build


def run_case(case_id: str, root: Path) -> CaseRun:
    fixture = build_fixture(case_id).start()
    try:
        contract = fixture.contract
        task = TGATask(
            id=f"eval_{case_id.lower()}_{uuid4().hex[:8]}",
            name=f"{case_id}: {contract.title}",
            mode=contract.task_mode,
            target=fixture.target,
            scope=fixture.scope,
            allow_active_scan=True,
            goal=contract.goal,
            flag_format=contract.flag_format,
        )
        task_root = root / task.id
        store = EvidenceStore(task_root / "evidence.db")
        try:
            store.create_task(task)
            artifact_store = ArtifactStore(task_root / "artifacts")
            executor = FixtureExecutor(
                fixture,
                ControlledActionExecutor(artifact_store=artifact_store),
            )
            manager = Manager(
                store=store,
                run_root=root,
                executor=executor,
                solver=ScriptedCaseSolver(
                    _actions_for(fixture),
                    "binary" if case_id == "W6" else "code" if case_id == "W5" else "web",
                ),
            )
            snapshot = manager.run_session(task.id)
        finally:
            store.close()
        snapshot["challenge_contract"] = contract.model_dump(mode="json")
        return CaseRun(fixture=fixture, snapshot=snapshot, run_root=root)
    finally:
        fixture.close()
