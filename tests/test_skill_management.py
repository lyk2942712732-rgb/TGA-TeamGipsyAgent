from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import app
from tga.skills.registry import SkillRegistry


CUSTOM_SKILL = b"""---
name: custom-web-proof
version: "1"
modes: [penetration_test]
capabilities: [http.request, artifact.inspect]
tags: [web, auth]
---
# When to use
Use after an authorization boundary is observed.

# Workflow
Compare a baseline and one bounded request, then preserve both results.
"""


def test_custom_skill_crud_is_scene_aware_and_runtime_visible(tmp_path: Path, monkeypatch) -> None:
    custom_root = tmp_path / "custom-skills"
    monkeypatch.setenv("TGA_CUSTOM_SKILLS_ROOT", str(custom_root))
    client = TestClient(app)

    created = client.post(
        "/api/v2/settings/skills/import",
        headers={"X-TGA-Filename": "custom-web-proof.md", "X-TGA-Scene": "penetration_test", "Content-Type": "text/markdown"},
        content=CUSTOM_SKILL,
    )
    assert created.status_code == 201, created.text
    assert created.json()["skill"]["editable"] is True
    assert (custom_root / "custom-web-proof.md").is_file()

    detail = client.get("/api/v2/settings/skills/custom-web-proof")
    assert detail.status_code == 200
    assert "authorization boundary" in detail.json()["skill"]["body"]
    assert {skill.name for skill in SkillRegistry().query(mode="penetration_test", tags=["auth"])} >= {"custom-web-proof"}
    assert "custom-web-proof" not in {skill.name for skill in SkillRegistry().query(mode="reverse_engineering")}

    updated = client.put("/api/v2/settings/skills/custom-web-proof", json={
        "modes": ["penetration_test", "ctf"],
        "capabilities": ["http.request"],
        "tags": ["web"],
        "version": "2",
        "body": "# Updated\nUse this revised workflow.",
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["skill"]["version"] == "2"
    assert "custom-web-proof" in {skill.name for skill in SkillRegistry().query(mode="ctf", tags=["web"])}

    deleted = client.delete("/api/v2/settings/skills/custom-web-proof")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert client.get("/api/v2/settings/skills/custom-web-proof").status_code == 404


def test_skill_management_rejects_builtin_overwrite_and_unsafe_uploads(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TGA_CUSTOM_SKILLS_ROOT", str(tmp_path / "skills"))
    client = TestClient(app)

    duplicate = CUSTOM_SKILL.replace(b"custom-web-proof", b"web-recon")
    response = client.post(
        "/api/v2/settings/skills/import",
        headers={"X-TGA-Filename": "web-recon.md"},
        content=duplicate,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SKILL_EXISTS"
    updated = client.put("/api/v2/settings/skills/web-recon", json={
        "modes": ["ctf"], "capabilities": [], "tags": [], "version": "2", "body": "changed",
    })
    assert updated.status_code == 200
    assert updated.json()["skill"]["source"] == "custom"
    assert client.delete("/api/v2/settings/skills/web-recon").status_code == 200
    assert client.get("/api/v2/settings/skills/web-recon").status_code == 404

    traversal = client.post(
        "/api/v2/settings/skills/import",
        headers={"X-TGA-Filename": "..%2Fescape.md"},
        content=CUSTOM_SKILL,
    )
    assert traversal.status_code == 422

    invalid_scene = client.post(
        "/api/v2/settings/skills/import",
        headers={"X-TGA-Filename": "bad.md"},
        content=CUSTOM_SKILL.replace(b"penetration_test", b"unknown_scene"),
    )
    assert invalid_scene.status_code == 422

    scene_mismatch = client.post(
        "/api/v2/settings/skills/import",
        headers={"X-TGA-Filename": "wrong-scene.md", "X-TGA-Scene": "reverse_engineering"},
        content=CUSTOM_SKILL.replace(b"custom-web-proof", b"wrong-scene"),
    )
    assert scene_mismatch.status_code == 422
    assert scene_mismatch.json()["detail"]["code"] == "SKILL_SCENE_MISMATCH"
