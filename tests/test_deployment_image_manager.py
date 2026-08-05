"""A deployment must not claim it prepared images it never looked at."""

from __future__ import annotations

import ast
import subprocess

import pytest

from tga.deployment import image_manager
from tga.deployment.errors import ErrorCode
from tga.deployment.paths import project_root
from tga.deployment.state import PROVISION_STEPS
from tga.sandbox.config import SandboxConfig

PINNED = "ghcr.io/example/tga-kali-ctf-web@sha256:" + "a" * 64
PLACEHOLDER = "ghcr.io/example/tga-kali-ctf-web@sha256:REPLACE_WITH_RELEASE_DIGEST"


def _config(**profile_overrides) -> SandboxConfig:
    profile = {
        "id": "ctf-web-v1",
        "provider": "sandboxd",
        "image": PINNED,
        "toolset_digest": "c" * 64,
    }
    profile.update(profile_overrides)
    return SandboxConfig.model_validate(
        {
            "version": 1,
            "runtime": "enforced",
            "sandboxd": {"allowed_client_uids": [1001]},
            "profiles": {profile["id"]: profile},
        }
    )


@pytest.fixture
def docker(monkeypatch):
    """Stand in for the docker CLI, recording what it was asked to do."""

    calls: list[list[str]] = []
    behaviour = {"present": False, "pull_ok": True, "pull_stderr": ""}

    def fake_run(args, **kwargs):
        calls.append(list(args))
        verb = args[1]
        if verb == "image":
            code = 0 if behaviour["present"] else 1
            return subprocess.CompletedProcess(args, code, "", "No such image")
        if verb == "pull":
            if behaviour["pull_ok"]:
                behaviour["present"] = True
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 1, "", behaviour["pull_stderr"])
        raise AssertionError(f"unexpected docker verb {verb}")

    monkeypatch.setattr(image_manager.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(image_manager.subprocess, "run", fake_run)
    return type("Docker", (), {"calls": calls, "behaviour": behaviour})()


def test_profiles_without_an_image_of_their_own_are_not_wanted():
    config = _config(id="remote-http", provider="remote_http", image=None, toolset_digest=None)
    assert image_manager.wanted_images(config) == []


def test_a_present_image_is_reported_without_pulling(docker):
    docker.behaviour["present"] = True

    report = image_manager.ensure_images(_config(), pull=True)

    assert report.ok
    assert report.statuses[0].present and not report.statuses[0].pulled
    assert [call[1] for call in docker.calls] == ["image"]


def test_a_missing_image_is_reported_rather_than_pulled_by_default(docker):
    report = image_manager.ensure_images(_config())

    assert not report.ok
    status = report.statuses[0]
    assert status.code is ErrorCode.PROFILE_IMAGE_MISSING
    assert [call[1] for call in docker.calls] == ["image"], "checking must not pull"


def test_a_missing_image_is_pulled_when_asked(docker):
    report = image_manager.ensure_images(_config(), pull=True)

    assert report.ok
    assert report.statuses[0].pulled
    assert [call[1] for call in docker.calls] == ["image", "pull"]


def test_a_failed_pull_carries_docker_own_reason(docker):
    docker.behaviour["pull_ok"] = False
    docker.behaviour["pull_stderr"] = "denied: requested access to the resource is denied"

    report = image_manager.ensure_images(_config(), pull=True)

    status = report.statuses[0]
    assert status.code is ErrorCode.PROFILE_IMAGE_MISSING
    assert "denied" in status.detail


def test_a_placeholder_digest_is_never_pulled(docker):
    """Pulling a placeholder would fail slowly and report the wrong problem."""
    report = image_manager.ensure_images(_config(image=PLACEHOLDER), pull=True)

    assert report.statuses[0].code is ErrorCode.PROFILE_DIGEST_INVALID
    assert report.unpinned
    assert docker.calls == []


def test_a_mutable_tag_is_treated_as_unpinned(docker):
    report = image_manager.ensure_images(
        _config(image="ghcr.io/example/tga-kali-ctf-web:latest"), pull=True
    )

    assert report.statuses[0].code is ErrorCode.PROFILE_DIGEST_INVALID
    assert docker.calls == []


def test_missing_docker_is_reported_without_pretending_to_check(monkeypatch):
    monkeypatch.setattr(image_manager.shutil, "which", lambda name: None)

    report = image_manager.ensure_images(_config(), pull=True)

    assert not report.docker_available
    assert report.statuses[0].code is ErrorCode.DOCKER_UNAVAILABLE
    assert not report.ok


def test_a_pull_timeout_does_not_escape(monkeypatch):
    """Sandbox availability is graded, so a slow registry degrades, not fails."""

    def timeout(args, **kwargs):
        if args[1] == "pull":
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 1))
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(image_manager.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(image_manager.subprocess, "run", timeout)

    report = image_manager.ensure_images(_config(), pull=True)

    assert report.statuses[0].code is ErrorCode.PROFILE_IMAGE_MISSING
    assert "timed out" in report.statuses[0].detail


def test_every_lifecycle_step_records_the_step_it_reports():
    """Marking one step's name while reporting another hides both.

    `_step_web_bundle` marked `ensure_images` complete while returning a
    result named `ensure_web_bundle`. Both names are legitimate members of
    PROVISION_STEPS, so no set-membership check could catch it -- and the
    effect was that a host which had never looked at an image recorded a fully
    provisioned deployment.
    """
    source = (project_root() / "tga" / "deployment" / "lifecycle.py").read_text(
        encoding="utf-8"
    )

    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("_step_"):
            continue

        marked: set[str] = set()
        reported: set[str] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            first = child.args[0] if child.args else None
            literal = first.value if isinstance(first, ast.Constant) else None
            if not isinstance(literal, str):
                continue
            if isinstance(child.func, ast.Attribute) and child.func.attr == "mark_completed":
                marked.add(literal)
            elif isinstance(child.func, ast.Name) and child.func.id == "StepResult":
                reported.add(literal)

        assert marked <= reported, f"{node.name} records {marked - reported} but never reports it"
        assert marked <= set(PROVISION_STEPS), f"{node.name} records an unknown step"
