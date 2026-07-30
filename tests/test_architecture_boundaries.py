from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

from pydantic import BaseModel

from tga import contracts
from tga.domain.evidence import legacy_models as evidence_models
from tga.domain.governance import models as governance_models
from tga.domain.solver import legacy_models as solver_models
from tga.domain.task import models as task_models


FORBIDDEN_DOMAIN_IMPORTS = {"fastapi", "sqlite3", "openai", "mcp"}


def test_domain_does_not_import_framework_or_infrastructure_implementations() -> None:
    domain_root = Path(__file__).parents[1] / "tga" / "domain"
    violations: list[str] = []
    for path in domain_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".", 1)[0].casefold()
                if root in FORBIDDEN_DOMAIN_IMPORTS:
                    violations.append(f"{path.relative_to(domain_root)} imports {name}")
    assert violations == []


def test_contracts_are_identity_preserving_compatibility_exports() -> None:
    for module in (task_models, governance_models, evidence_models, solver_models):
        for name in module.__all__:
            canonical = getattr(module, name)
            if inspect.isclass(canonical) and issubclass(canonical, BaseModel):
                assert getattr(contracts, name) is canonical


def test_task_json_shape_remains_stable_through_compatibility_export() -> None:
    task = contracts.TGATask(id="architecture_snapshot", name="snapshot", mode="ctf", goal="solve")
    payload = task.model_dump(mode="json")

    assert payload["id"] == "architecture_snapshot"
    assert payload["schema_version"] == 6
    assert payload["session_input"] == {"prompt": "", "files": []}
    assert payload["execution_policy"] == {
        "preset": "offline_analysis",
        "network": {
            "access": "disabled", "interaction": "observe", "seed_origins": [],
                "custom_origins": [], "custom_domains": [], "custom_cidrs": [],
                "custom_ports": [],
            "deny_private_networks": True, "deny_loopback": True,
            "deny_link_local": True, "deny_cloud_metadata": True,
            "rate_limit_per_minute": 30, "concurrency": 2,
            "request_timeout_seconds": 30,
        },
        "local_compute": {
            "mode": "disabled", "timeout_seconds": 120, "concurrency": 2,
            "network_inheritance": "task_network_policy",
        },
        "high_impact": {"mode": "forbidden", "allowed_actions": []},
    }


def test_new_package_skeleton_is_importable() -> None:
    for name in (
        "tga.application",
        "tga.application.ports",
        "tga.runtime.agents",
        "tga.runtime.orchestration",
        "tga.runtime.context",
        "tga.runtime.tooling",
        "tga.runtime.scheduling",
        "tga.infrastructure",
        "tga.shared",
        "tga.bootstrap.container",
    ):
        assert importlib.import_module(name) is not None
