from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from apps.api.main import app
from apps.api.routes import capabilities as capability_routes
from apps.api.routes import kali as kali_routes
from tga.application.capabilities import (
    CapabilityAssignmentService,
    HostCapabilityRegistry,
)
from tga.application.kali import (
    KaliProfileCreateCommand,
    KaliProfileService,
    KaliProfileUpdateCommand,
)
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.runtime.host_handler_registry import HostHandlerRegistry


def test_solver_tools_and_profile_apis_share_kali_assignments() -> None:
    client = TestClient(app)
    solver = client.get("/api/v2/solvers/ctf-pwn-solver")
    capabilities = client.get("/api/v2/capabilities/kali")
    profile = client.get("/api/v2/kali/profiles/ctf-pwn-v1")
    profile_solvers = client.get("/api/v2/kali/profiles/ctf-pwn-v1/solvers")

    assert solver.status_code == capabilities.status_code == profile.status_code == 200
    solver_payload = solver.json()
    capability_items = {item["id"]: item for item in capabilities.json()["items"]}
    profile_payload = profile.json()

    assert solver_payload["kali"]["capabilities"] == ["kali.exec", "kali.session"]
    assert "ctf-pwn-solver" in capability_items["kali.exec"]["assigned_solver_ids"]
    assert "ctf-pwn-solver" in capability_items["kali.session"]["assigned_solver_ids"]
    assert profile_payload["assigned_solver_ids"] == profile_solvers.json()["solver_ids"]
    assert "ctf-pwn-solver" in profile_payload["assigned_solver_ids"]


def test_process_health_succeeds_when_kali_is_not_ready() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["process"] == "healthy"
    assert response.json()["kali_runtime"] == "not_ready"


def test_solver_kali_health_reports_selected_profile_only() -> None:
    client = TestClient(app)
    pwn = client.get("/api/v2/solvers/ctf-pwn-solver/kali-health")
    reporter = client.get("/api/v2/solvers/security-reporter/kali-health")
    summary = client.get("/api/v2/solvers/kali-health")

    assert pwn.status_code == reporter.status_code == summary.status_code == 200
    assert pwn.json()["profile_id"] == "ctf-pwn-v1"
    assert reporter.json()["status"] == "host_only"
    items = {item["solver_id"]: item for item in summary.json()["items"]}
    assert items["security-reporter"]["status"] == "host_only"
    # A Kali-backed solver's verdict depends on the host running the tests --
    # no sandboxd, no image pulled -- so this asserts the routing and that the
    # summary agrees with the per-solver view, not one particular verdict.
    # Pinning it to "unresolved_digest" only held while the shipped config
    # carried placeholders.
    assert items["ctf-pwn-solver"]["status"] == pwn.json()["status"]
    assert pwn.json()["status"] != "host_only"
    assert pwn.json()["reasons"], "a profile that is not ready must say why"


def test_solver_kali_deep_check_returns_501() -> None:
    response = TestClient(app).post(
        "/api/v2/solvers/ctf-pwn-solver/kali-health/check"
    )

    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "kali_deep_check_not_implemented"


def test_host_profile_catalog_exposes_editable_solver_profiles() -> None:
    response = TestClient(app).get("/api/v2/capabilities/host-profiles")

    assert response.status_code == 200
    profiles = {item["id"]: item for item in response.json()["items"]}
    assert "worker-default" in profiles
    assert "input.read" in profiles["worker-default"]["capability_ids"]


def test_handler_api_status_and_runtime_routing_share_registry_contract(
    monkeypatch,
) -> None:
    assignments = CapabilityAssignmentService()
    target = assignments.host_registry.require("input.read")
    changed = target.model_copy(update={"handler_key": "missing.contract.handler"})
    registry = HostCapabilityRegistry(
        tuple(
            changed if item.id == target.id else item
            for item in assignments.host_registry.all()
        ),
        assignments.host_registry.profiles(),
    )
    changed_assignments = CapabilityAssignmentService(host_registry=registry)
    monkeypatch.setattr(
        capability_routes, "_assignments", lambda: changed_assignments
    )

    response = TestClient(app).get("/api/v2/capabilities/host/input.read")
    assert response.status_code == 200
    assert response.json()["handler_status"] == "missing"

    handlers = HostHandlerRegistry(host_registry=registry)
    assert handlers.missing(("input.read",)) == ("input.read",)
    with pytest.raises(KeyError, match="no registered runtime handler"):
        handlers.resolve("input.read")
    handlers.register("input.read", lambda request: request)
    assert handlers.execute("input.read", "routed") == "routed"


def test_solver_update_api_rejects_removed_kali_fields() -> None:
    client = TestClient(app)
    definition = client.get("/api/v2/solvers/ctf-pwn-solver").json()
    response = client.put(
        "/api/v2/solvers/ctf-pwn-solver/capabilities",
        json={
            "expected_content_sha256": definition["content_sha256"],
            "host_capability_profile_id": "worker-default",
            "host_capability_overrides": {"add": [], "remove": []},
            "kali": {
                "profile_id": "ctf-pwn-v1",
                "allow_exec": True,
                "allow_session": True,
            },
        },
    )

    assert response.status_code == 422


def test_solver_update_rejects_stale_definition_revision(
    tmp_path: Path, monkeypatch
) -> None:
    source = Path(__file__).parents[1] / "resources" / "solver_definitions"
    root = tmp_path / "solver-definitions"
    shutil.copytree(source, root)
    monkeypatch.setenv("TGA_SOLVER_DEFINITION_ROOT", str(root))
    client = TestClient(app)
    definition = client.get("/api/v2/solvers/ctf-pwn-solver").json()
    payload = {
        "expected_content_sha256": definition["content_sha256"],
        "host_capability_profile_id": definition["host_capability_profile_id"],
        "host_capability_overrides": {
            "add": definition["host_capability_overrides"]["add"],
            "remove": ["artifact.publish"],
        },
        "kali": {
            "profile_id": definition["kali"]["profile_id"],
            "capabilities": definition["kali"]["capabilities"],
        },
    }

    first = client.put(
        "/api/v2/solvers/ctf-pwn-solver/capabilities", json=payload
    )
    stale = client.put(
        "/api/v2/solvers/ctf-pwn-solver/capabilities", json=payload
    )

    assert first.status_code == 200, first.text
    assert stale.status_code == 409


def test_manifest_preview_matches_solver_kali_projection() -> None:
    client = TestClient(app)
    solver = client.get("/api/v2/solvers/ctf-pwn-solver").json()
    manifest = client.get(
        "/api/v2/solvers/ctf-pwn-solver/manifest-preview",
        params={"mode": "ctf"},
    ).json()

    assert manifest["kali"]["profile_id"] == solver["kali"]["profile_id"]
    assert manifest["kali"]["capabilities"] == solver["kali"]["capabilities"]
    assert {item["id"] for item in manifest["host_capabilities"]} == {
        item["id"] for item in solver["host_capabilities"]
    }


def test_kali_profile_crud_and_bound_delete_conflict(
    tmp_path: Path, monkeypatch
) -> None:
    source = Path(__file__).parents[1] / "config" / "sandbox.json"
    config_path = tmp_path / "sandbox.json"
    config_path.write_bytes(source.read_bytes())
    profiles = KaliProfileService(config_path=config_path)

    def assignments() -> CapabilityAssignmentService:
        return CapabilityAssignmentService(kali_profiles=profiles)

    monkeypatch.setattr(kali_routes, "_assignments", assignments)
    client = TestClient(app)
    template = client.get("/api/v2/kali/profiles/ctf-classifier-v1").json()
    template.update({
        "id": "temporary-test-v1",
        "display_name": "Temporary test",
        "assigned_solver_count": 0,
        "assigned_solver_ids": [],
    })

    created = client.post("/api/v2/kali/profiles", json=template)
    assert created.status_code == 201, created.text
    assert created.json()["assigned_solver_ids"] == []

    template = created.json()
    template["allowed_executables"] = [
        *template["allowed_executables"], "temporary-tool",
    ]
    updated = client.put("/api/v2/kali/profiles/temporary-test-v1", json=template)
    assert updated.status_code == 200, updated.text
    assert "temporary-tool" in updated.json()["allowed_executables"]

    conflict = client.delete("/api/v2/kali/profiles/ctf-pwn-v1")
    assert conflict.status_code == 409
    deleted = client.delete("/api/v2/kali/profiles/temporary-test-v1")
    assert deleted.status_code == 204
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert "temporary-test-v1" not in persisted["profiles"]


def test_kali_profile_service_rejects_stale_file_revision(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "config" / "sandbox.json"
    config_path = tmp_path / "sandbox.json"
    config_path.write_bytes(source.read_bytes())
    first = KaliProfileService(config_path=config_path)
    stale = KaliProfileService(config_path=config_path)
    first_profile = first.detail("ctf-classifier-v1", assigned_solver_ids=())
    stale_profile = stale.detail("ctf-classifier-v1", assigned_solver_ids=())

    first.update(
        first_profile.id,
        KaliProfileUpdateCommand.model_validate(
            first_profile.model_copy(
                update={"display_name": "First writer"}
            ).model_dump(mode="json")
        ),
    )

    with pytest.raises(PersistenceConflict):
        stale.update(
            stale_profile.id,
            KaliProfileUpdateCommand.model_validate(
                stale_profile.model_copy(
                    update={"display_name": "Stale writer"}
                ).model_dump(mode="json")
            ),
        )


def test_kali_profile_get_put_get_round_trip(tmp_path: Path, monkeypatch) -> None:
    source = Path(__file__).parents[1] / "config" / "sandbox.json"
    config_path = tmp_path / "sandbox.json"
    config_path.write_bytes(source.read_bytes())
    profiles = KaliProfileService(config_path=config_path)
    monkeypatch.setattr(
        kali_routes,
        "_assignments",
        lambda: CapabilityAssignmentService(kali_profiles=profiles),
    )
    client = TestClient(app)

    infrastructure_before = profiles.config.profile("ctf-classifier-v1")
    before = client.get("/api/v2/kali/profiles/ctf-classifier-v1").json()
    before["display_name"] = "Classifier test profile"
    before["limits"]["memory_mb"] += 64
    response = client.put(
        "/api/v2/kali/profiles/ctf-classifier-v1", json=before
    )

    assert response.status_code == 200, response.text
    after = client.get("/api/v2/kali/profiles/ctf-classifier-v1").json()
    assert after["limits"]["memory_mb"] == before["limits"]["memory_mb"]
    assert after["display_name"] == before["display_name"]
    assert after["image_name"] == before["image_name"]
    assert after["network_mode"] == before["network_mode"]
    infrastructure_after = profiles.config.profile("ctf-classifier-v1")
    assert infrastructure_after.provider == infrastructure_before.provider
    assert infrastructure_after.allow_net_raw == infrastructure_before.allow_net_raw
    assert infrastructure_after.allow_ptrace == infrastructure_before.allow_ptrace
    assert infrastructure_after.toolset_digest == infrastructure_before.toolset_digest
    assert (
        infrastructure_after.limits.max_output_bytes
        == infrastructure_before.limits.max_output_bytes
    )


def test_profile_verify_does_not_report_placeholder_success() -> None:
    response = TestClient(app).post(
        "/api/v2/kali/profiles/ctf-classifier-v1/verify"
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["status"] == "not_implemented"

    refreshed = TestClient(app).post(
        "/api/v2/kali/profiles/ctf-classifier-v1/refresh-tool-inventory"
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["ok"] is False
    assert refreshed.json()["status"] == "not_implemented"


def test_profile_crud_does_not_persist_runtime_environment_override(
    tmp_path: Path, monkeypatch
) -> None:
    source = Path(__file__).parents[1] / "config" / "sandbox.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["runtime"] = "enforced"
    payload["docker_sandbox"]["template"] = (
        "example.invalid/tga/template@sha256:" + "a" * 64
    )
    payload["sandboxd"]["allowed_client_uids"] = [1000]
    for profile in payload["profiles"].values():
        if profile["provider"] != "remote_http":
            image = str(profile["image"]).split("@sha256:", 1)[0]
            profile["image"] = image + "@sha256:" + "b" * 64
    config_path = tmp_path / "sandbox.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("TGA_SANDBOX_RUNTIME", "disabled")
    profiles = KaliProfileService(config_path=config_path)
    assert profiles.config.runtime == "disabled"
    assert profiles.persisted_config.runtime == "enforced"

    template = profiles.detail(
        "ctf-classifier-v1", assigned_solver_ids=()
    ).model_copy(update={"id": "environment-audit-v1"})
    profiles.create(
        KaliProfileCreateCommand.model_validate(template.model_dump(mode="json"))
    )

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["runtime"] == "enforced"
    assert "environment-audit-v1" in persisted["profiles"]
