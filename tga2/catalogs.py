"""Frontend catalogs derived from the actual TGA2 graph and tools."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tga2.config import (
    KALI_PROFILE_ID,
    GraphSettings,
    KaliSandboxSettings,
    RuntimeSettings,
)

ROLES = ("supervisor", "worker", "reviewer", "reporter")


def host_capabilities() -> list[dict[str, Any]]:
    definitions = [
        ("list_inputs", "List task inputs", "workspace", "passive", {}, ROLES),
        (
            "read_input",
            "Read task input",
            "workspace",
            "passive",
            {"path": {"type": "string"}},
            ("worker",),
        ),
        (
            "glob_search",
            "Find task files",
            "workspace",
            "passive",
            {"pattern": {"type": "string"}},
            ("worker", "reviewer"),
        ),
        (
            "grep_search",
            "Search task file content",
            "workspace",
            "passive",
            {"pattern": {"type": "string"}},
            ("worker", "reviewer"),
        ),
        (
            "save_note",
            "Save analysis note",
            "evidence",
            "active",
            {"content": {"type": "string"}},
            ("worker",),
        ),
        (
            "run_command",
            "Run isolated command",
            "sandbox",
            "active",
            {"command": {"type": "string"}},
            ("worker",),
        ),
    ]
    return [
        {
            "id": name,
            "display_name": label,
            "category": category,
            "description": label,
            "allowed_roles": list(roles),
            "risk": risk,
            "input_schema": {"type": "object", "properties": properties},
            "output_schema": {"type": "string"},
            "handler_key": name,
            "handler_status": "ready",
            "assigned_solver_count": len(roles),
            "assigned_solver_ids": list(roles),
        }
        for name, label, category, risk, properties, roles in definitions
    ]


def solver_definitions(modes: tuple[str, ...], runtime: RuntimeSettings) -> list[dict[str, Any]]:
    capabilities = host_capabilities()
    by_role = {
        role: [item for item in capabilities if role in item["allowed_roles"]]
        for role in ROLES
    }
    return [
        {
            "id": role,
            "version": "2.0",
            "role": role,
            "specialties": ["planning"] if role == "supervisor" else ["evidence"],
            "supported_modes": list(modes),
            "supported_subtypes": [],
            "system_prompt_template": f"TGA2 {role} prompt is assembled by LangChain middleware.",
            "default_skill_tags": [],
            "required_skill_names": [],
            "host_capability_profile_id": f"{role}-default",
            "host_capability_overrides": {"add": [], "remove": []},
            "host_capabilities": [
                {
                    "id": item["id"],
                    "display_name": item["display_name"],
                    "category": item["category"],
                    "risk": item["risk"],
                    "source": "host",
                }
                for item in by_role[role]
            ],
            "kali": None,
            "accepted_intent_kinds": ["task"],
            "output_contract": {
                "name": f"{role}_draft",
                "required_fields": ["summary"],
            },
            "default_budget": {
                "max_turns": (
                    runtime.budget.roles.worker.calls_per_attempt
                    if role == "worker"
                    else {
                        "supervisor": runtime.budget.roles.supervisor.calls_per_decision,
                        "reviewer": runtime.budget.roles.reviewer.calls_per_review,
                        "reporter": runtime.budget.roles.reporter.calls_per_report,
                    }[role]
                ),
                "max_tool_calls": (
                    runtime.budget.roles.worker.tool_calls_per_attempt
                    if role == "worker"
                    else 0
                ),
            },
            "completion_authority": "reviewer" if role == "reviewer" else "none",
            "content_sha256": _hash(
                {"role": role, "tools": [item["id"] for item in by_role[role]]}
            ),
        }
        for role in ROLES
    ]


def kali_profiles(settings: KaliSandboxSettings) -> list[dict[str, Any]]:
    value = {
        "id": settings.profile_id,
        "display_name": "TGA2 isolated Kali",
        "image_name": settings.image,
        "image_tag": "",
        "image_digest": settings.expected_digest,
        "image": settings.image,
        "image_role": "universal",
        "shared_image_profile_count": 1,
        "tools": [],
        "supported_capabilities": ["kali.exec"],
        "allowed_executables": [],
        "session_executables": [],
        "network_mode": "none",
        "input_mount": "/workspace",
        "scratch_mount": "/tmp",
        "shared_artifact_mount": "/artifacts",
        "limits": {
            "cpu_cores": settings.cpu_cores,
            "memory_mb": settings.memory_mb,
            "timeout_seconds": settings.command_timeout_seconds,
            "max_processes": settings.max_processes,
        },
        "enabled": settings.enabled,
        "assigned_solver_count": 1,
        "assigned_solver_ids": ["worker"],
    }
    value["config_sha256"] = _hash(value)
    return [value]


def kali_capabilities() -> list[dict[str, Any]]:
    return [
        {
            "id": "kali.exec",
            "display_name": "Isolated command execution",
            "description": "Runs a command through the configured Sandbox adapter.",
            "risk": "active",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
            },
            "assigned_solver_count": 1,
            "assigned_solver_ids": ["worker"],
            "profile_ids": [KALI_PROFILE_ID],
        }
    ]


def team_templates(modes: tuple[str, ...], graph: GraphSettings) -> list[dict[str, Any]]:
    return [
        {
            "mode": mode,
            "supervisor_definition_id": "supervisor",
            "required_solver_definition_ids": ["worker", "reviewer", "reporter"],
            "available_solver_definition_ids": list(ROLES),
            "reviewer_definition_id": "reviewer",
            "reporter_definition_id": "reporter",
            "spawn_rules": [],
            "max_active_workers": graph.max_active_workers,
            "max_total_solvers": graph.max_total_solvers,
            "completion_policy": {
                "review_required": True,
                "evidence_required_for_findings": True,
            },
            "content_sha256": _hash({"mode": mode, "roles": ROLES}),
        }
        for mode in modes
    ]


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


__all__ = [
    "host_capabilities",
    "kali_capabilities",
    "kali_profiles",
    "solver_definitions",
    "team_templates",
]
