"""High-signal solver context assembly without model-provider details."""

from __future__ import annotations

from typing import Any

from tga.contracts import SolverRole, TGATask


MAX_MEMORY_ITEMS = 20
MAX_ACTION_SUMMARIES = 6


ROLE_INSTRUCTIONS: dict[SolverRole, str] = {
    "recon": "Map assets, endpoints, forms and protocols; report coverage gaps and avoid speculative exploitation.",
    "targeted": "Test exactly one active hypothesis with the smallest evidence-producing action.",
    "research": "Turn observed versions, errors or protocols into one executable next test; do not announce vulnerabilities.",
    "main": "Coordinate priorities and structured child tasks; do not bypass the evidence gate.",
}


def build_solver_context(
    *, task: TGATask, snapshot: dict[str, Any], skills: list[Any] | None = None,
    role: SolverRole = "main", solver_id: str | None = None,
) -> dict[str, Any]:
    """Return compact, evidence-linked context for one solver turn.

    Raw artifacts, HTTP bodies, tool stdout, secrets, and chat transcripts are
    intentionally excluded.  A solver can request a specific artifact through
    the controlled executor when it needs detail.
    """
    board = snapshot.get("board") or {}
    memory = (board.get("memory") or [])[-MAX_MEMORY_ITEMS:]
    hypotheses = [
        item for item in (board.get("hypotheses") or [])
        if item.get("status") in {"pending", "testing", "inconclusive"}
    ]
    actions = (snapshot.get("actions") or [])[-MAX_ACTION_SUMMARIES:]
    return {
        "task": {
            "id": task.id, "mode": task.mode, "target": task.target, "scope": task.scope,
            "goal": task.goal, "intensity": task.intensity, "flag_format": task.flag_format,
        },
        "session": snapshot.get("session") or {},
        "challenge": snapshot.get("challenge") or {},
        "solver": {"id": solver_id, "role": role},
        "hypotheses": hypotheses,
        "memory": [
            {"id": item.get("id"), "kind": item.get("kind"), "content": item.get("content"),
             "artifact_ids": item.get("artifact_ids") or [], "source": item.get("source")}
            for item in memory
        ],
        "recent_actions": [
            {"id": item.get("id"), "capability": item.get("capability"), "target": item.get("target"),
             "status": item.get("status"), "hypothesis_id": item.get("hypothesis_id"),
             "result": {key: (item.get("result") or {}).get(key) for key in ("summary", "artifact_ids", "facts", "leads", "error")}}
            for item in actions
        ],
        "skills": [
            {
                "name": skill.name,
                "version": skill.version,
                "source": skill.source,
                "capabilities": skill.capabilities,
                "tags": skill.tags,
                "summary": skill.summary,
            }
            for skill in (skills or [])[:3]
        ],
        "instruction": f"{ROLE_INSTRUCTIONS[role]} Propose only one evidence-linked ActionSpec for an active hypothesis; do not claim a flag or finding without an artifact.",
    }
