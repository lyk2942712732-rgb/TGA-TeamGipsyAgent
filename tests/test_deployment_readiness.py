"""Readiness grading decides whether `tga up` may claim success."""

from __future__ import annotations

import pytest

from tga.deployment import readiness
from tga.deployment.errors import ErrorCode
from tga.deployment.readiness import Check, ReadinessReport


def _report(**overrides) -> ReadinessReport:
    defaults = {
        "api": Check("api", "ready"),
        "storage": Check("storage", "ready"),
        "sandbox_runtime": "enforced",
        "sandbox": [Check(name, "ready") for name in
                    ("sandboxd", "docker", "runsc", "nftables", "cgroup_v2")],
        "profiles": [Check("ctf-web-v1", "ready")],
    }
    return ReadinessReport(**{**defaults, **overrides})


def test_fully_provisioned_deployment_is_ready():
    report = _report()
    assert report.status == "ready"
    assert report.ready


def test_broken_storage_fails_rather_than_degrades():
    """Without writable storage no task can run, so serving is pointless."""
    report = _report(storage=Check("storage", "unavailable", "", ErrorCode.RUN_ROOT_UNWRITABLE))
    assert report.status == "failed"
    assert not report.ready


def test_disabled_sandbox_degrades_but_still_serves():
    """The operator's chosen posture: degraded startup is still a startup."""
    report = _report(
        sandbox_runtime="disabled",
        sandbox=[Check(name, "disabled") for name in ("sandboxd", "docker")],
    )
    assert report.status == "degraded"
    assert report.ready is True


def test_missing_isolation_capability_degrades_an_enforced_deployment():
    report = _report(
        sandbox=[
            Check("sandboxd", "ready"),
            Check("docker", "ready"),
            Check("runsc", "unavailable", "", ErrorCode.RUNSC_NOT_REGISTERED),
        ]
    )
    assert report.status == "degraded"
    assert report.ready
    assert any(check.code is ErrorCode.RUNSC_NOT_REGISTERED for check in report.failures())


def test_unpinned_profile_degrades_an_enforced_deployment():
    report = _report(
        profiles=[Check("ctf-web-v1", "unavailable", "", ErrorCode.PROFILE_DIGEST_INVALID)]
    )
    assert report.status == "degraded"


def test_disabled_checks_are_not_reported_as_failures():
    """A deliberately disabled capability is a posture, not a fault."""
    report = _report(
        sandbox_runtime="disabled",
        sandbox=[Check("sandboxd", "disabled", "", ErrorCode.SANDBOX_RUNTIME_DISABLED)],
    )
    assert report.failures() == []


def test_serialised_payload_matches_the_launcher_contract():
    payload = _report().to_dict()
    assert payload["ready"] is True
    assert payload["status"] == "ready"
    assert payload["sandbox"]["runtime"] == "enforced"
    assert payload["sandbox"]["runsc"]["status"] == "ready"
    assert payload["profiles"]["ctf-web-v1"]["status"] == "ready"
    assert payload["errors"] == []


def test_errors_carry_codes_for_the_launcher():
    payload = _report(
        sandbox=[Check("docker", "unavailable", "daemon down", ErrorCode.DOCKER_UNAVAILABLE)]
    ).to_dict()
    assert payload["errors"] == [
        {"component": "docker", "code": "DOCKER_UNAVAILABLE", "detail": "daemon down"}
    ]


def test_evaluate_reports_this_installation(monkeypatch, tmp_path):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    payload = readiness.evaluate().to_dict()
    assert payload["api"]["status"] == "ready"
    assert payload["storage"]["status"] == "ready"
    assert payload["status"] in {"ready", "degraded", "failed"}


def test_port_probe_detects_a_bound_listener():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert not readiness.port_is_free("127.0.0.1", port)
    assert readiness.port_is_free("127.0.0.1", port)
