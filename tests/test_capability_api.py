from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.routes import kali as kali_routes
from tga.application.capabilities import CapabilityAssignmentService
from tga.application.kali import KaliProfileService


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


def test_solver_update_api_rejects_removed_kali_fields() -> None:
    response = TestClient(app).put(
        "/api/v2/solvers/ctf-pwn-solver/capabilities",
        json={
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
    template = profiles.config.profile("ctf-classifier-v1").model_dump(mode="json")
    template.update({
        "id": "temporary-test-v1",
        "image": "example.invalid/tga/test:1",
        "toolset_digest": "a" * 64,
    })

    created = client.post("/api/v2/kali/profiles", json=template)
    assert created.status_code == 201, created.text
    assert created.json()["assigned_solver_ids"] == []

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

    template = profiles.config.profile("ctf-classifier-v1").model_copy(
        update={"id": "environment-audit-v1"}
    )
    profiles.create(template)

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["runtime"] == "enforced"
    assert "environment-audit-v1" in persisted["profiles"]
