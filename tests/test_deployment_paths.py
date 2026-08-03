"""Path resolution is the contract that stops split-brain deployments."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tga.deployment import paths
from tga.deployment.errors import DeploymentError, ErrorCode


def test_run_root_prefers_explicit_argument_over_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "from-env"))
    explicit = tmp_path / "explicit"
    assert paths.run_root(explicit) == explicit.resolve()


def test_run_root_uses_environment_when_no_argument(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "configured"))
    assert paths.run_root() == (tmp_path / "configured").resolve()


def test_run_root_is_always_absolute(monkeypatch, tmp_path):
    """A later chdir must not silently repoint an already-resolved root."""
    monkeypatch.setenv("TGA_RUN_ROOT", "runs")
    monkeypatch.chdir(tmp_path)
    resolved = paths.run_root()
    assert resolved.is_absolute()
    assert resolved == (tmp_path / "runs").resolve()


def test_every_consumer_agrees_on_one_run_root(tmp_path, monkeypatch):
    """The P0 regression: API, container and sandbox must not diverge."""
    configured = tmp_path / "shared-root"
    monkeypatch.setenv("TGA_RUN_ROOT", str(configured))

    from apps.api.routes.support import _run_root
    from tga.bootstrap.container import Container
    from tga.sandbox.lifecycle import SandboxLifecycleService

    expected = configured.resolve()
    assert _run_root() == expected
    assert Container().run_root == expected
    # Constructing the lifecycle service must not require a live sandbox.
    service = SandboxLifecycleService.__new__(SandboxLifecycleService)
    service.run_root = paths.run_root()
    assert service.run_root == expected


def test_ensure_run_root_creates_and_proves_writability(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "runs"
    monkeypatch.setenv("TGA_RUN_ROOT", str(target))
    assert paths.ensure_run_root() == target.resolve()
    assert target.is_dir()
    assert not (target / ".tga-write-probe").exists()


def test_ensure_run_root_reports_unwritable_root(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("TGA_RUN_ROOT", str(blocker / "runs"))
    with pytest.raises(DeploymentError) as excinfo:
        paths.ensure_run_root()
    assert excinfo.value.code is ErrorCode.RUN_ROOT_UNWRITABLE
    assert excinfo.value.remediation


def test_web_dist_prefers_explicit_then_environment(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    from_env = tmp_path / "from-env"
    for candidate in (explicit, from_env):
        candidate.mkdir()
        (candidate / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setenv("TGA_WEB_DIST", str(from_env))

    assert paths.web_dist(explicit) == explicit.resolve()
    assert paths.web_dist() == from_env.resolve()


def test_web_dist_ignores_a_directory_without_index_html(tmp_path, monkeypatch):
    """An empty output directory is not a usable bundle."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("TGA_WEB_DIST", str(empty))
    monkeypatch.setattr(paths, "project_root", lambda: tmp_path / "absent")
    monkeypatch.setattr(
        paths, "_web_dist_candidates", lambda explicit: [empty], raising=True
    )
    with pytest.raises(DeploymentError) as excinfo:
        paths.web_dist()
    assert excinfo.value.code is ErrorCode.WEB_BUNDLE_MISSING


def test_state_and_log_dirs_follow_explicit_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TGA_LOG_DIR", str(tmp_path / "log"))
    assert paths.state_dir() == (tmp_path / "state").resolve()
    assert paths.log_dir() == (tmp_path / "log").resolve()
