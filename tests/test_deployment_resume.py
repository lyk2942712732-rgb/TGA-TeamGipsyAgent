"""A deployment that came up short has to be repairable by `tga up`.

Reported from a first install on someone else's machine.  `tga up` finished
with `0/1 images present` and told the operator to run `tga up --pull-images`.
That command printed `already_running` and did nothing, every time: the
short-circuit fired before the flag was read, so the one documented remedy
could not reach the step that owed the work.
"""

from __future__ import annotations

from tga.deployment import lifecycle, service_manager, state as state_module
from tga.deployment.lifecycle import StepResult


def _stub_steps(monkeypatch) -> dict[str, object]:
    """Replace every provisioning step with a recorder."""
    seen: dict[str, object] = {"ran": []}

    def record(name, **extra):
        seen["ran"].append(name)
        seen.update(extra)
        return StepResult(name, True, "")

    monkeypatch.setattr(lifecycle, "_step_detect_platform",
                        lambda current: record("detect_platform"))
    monkeypatch.setattr(lifecycle, "_step_configuration",
                        lambda current: record("ensure_configuration"))
    monkeypatch.setattr(lifecycle, "_step_web_bundle",
                        lambda current: record("ensure_web_bundle"))
    monkeypatch.setattr(lifecycle, "_step_container_engine",
                        lambda current: record("start_container_engine"))
    monkeypatch.setattr(lifecycle, "_step_images",
                        lambda current, *, pull: record("ensure_images", pull=pull))
    monkeypatch.setattr(lifecycle, "_step_sandboxd",
                        lambda current: record("start_sandboxd"))
    monkeypatch.setattr(lifecycle, "_step_start_api",
                        lambda current, *, host, port: record("start_api"))
    monkeypatch.setattr(
        lifecycle, "_step_wait_readiness",
        lambda current, *, timeout_seconds: (
            record("wait_for_readiness"), {"status": "degraded"}
        ),
    )
    monkeypatch.setattr(lifecycle, "_open_browser", lambda url: None)
    monkeypatch.setattr(lifecycle, "_fetch_readiness", lambda url: {"status": "ready"})
    monkeypatch.setattr(lifecycle, "_already_serving", lambda current: True)
    monkeypatch.setattr(service_manager, "manages_api", lambda: False)
    return seen


def _serving(monkeypatch, tmp_path, phase: str) -> None:
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path))
    current = state_module.load()
    current.phase = phase
    current.api_pid = 4242
    current.api_url = "http://127.0.0.1:8123"
    state_module.save(current)


def test_a_degraded_deployment_is_resumed_not_reported(monkeypatch, tmp_path):
    """Degraded means a step did not finish; `tga up` is how it finishes."""
    _serving(monkeypatch, tmp_path, "degraded")
    seen = _stub_steps(monkeypatch)

    result = lifecycle.up(open_browser=False)

    assert seen["ran"], "a degraded deployment was short-circuited instead of resumed"
    assert "ensure_images" in seen["ran"]
    assert [step.name for step in result.steps] != ["already_running"]


def test_pull_images_is_honoured_on_a_running_deployment(monkeypatch, tmp_path):
    """The flag asks for work.  Reporting "already running" refuses it."""
    _serving(monkeypatch, tmp_path, "ready")
    seen = _stub_steps(monkeypatch)

    lifecycle.up(open_browser=False, pull_images=True)

    assert "ensure_images" in seen["ran"], "--pull-images never reached the pull step"
    assert seen["pull"] is True, "the step ran but was not told to pull"


def test_a_healthy_deployment_is_still_left_alone(monkeypatch, tmp_path):
    """The idempotency the fix must not cost: `tga up` twice is not a restart."""
    _serving(monkeypatch, tmp_path, "ready")
    seen = _stub_steps(monkeypatch)

    result = lifecycle.up(open_browser=False)

    assert seen["ran"] == [], "a ready deployment was restarted"
    assert [step.name for step in result.steps] == ["already_running"]
    assert result.ok
