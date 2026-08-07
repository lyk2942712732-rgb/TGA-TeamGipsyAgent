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


_PINNED = "ghcr.io/org/tga-kali-ctf-web@sha256:" + "a" * 64


class _Profile:
    def __init__(self, image: str, provider: str = "docker_sandbox"):
        self.image = image
        self.provider = provider


class _SandboxConfig:
    def __init__(self, runtime: str = "enforced"):
        self.runtime = runtime
        self.profiles = {"ctf-web-v1": _Profile(_PINNED)}


def _store(monkeypatch, digests):
    """Pin what the local container store answers, and nothing else."""
    monkeypatch.setattr(
        "tga.sandbox.config.load_sandbox_config", lambda *a, **k: (_SandboxConfig(), None)
    )
    monkeypatch.setattr(readiness, "_local_image_digests", lambda: digests)


def test_an_empty_image_store_makes_every_profile_unavailable(monkeypatch):
    """The regression: a host with no images at all graded them all ready.

    `_local_image_digests` returned an empty set both when the store held
    nothing and when it could not be read, and the guard `if local_images and
    ...` then skipped the comparison entirely.  A first install reported
    `0/1 images present` and `status: ready` in the same run.
    """
    _store(monkeypatch, set())

    checks = readiness._check_profiles()

    assert [check.status for check in checks] == ["unavailable"]
    assert checks[0].code is ErrorCode.PROFILE_IMAGE_MISSING
    assert _report(profiles=checks).status == "degraded"


def test_an_unlistable_image_store_is_not_read_as_ready(monkeypatch):
    """No answer is not a good answer; it may not be graded as one."""
    _store(monkeypatch, None)

    checks = readiness._check_profiles()

    assert [check.status for check in checks] == ["unknown"]
    assert _report(profiles=checks).status == "degraded"


def test_a_present_image_is_ready(monkeypatch):
    _store(monkeypatch, {"sha256:" + "a" * 64})

    checks = readiness._check_profiles()

    assert [check.status for check in checks] == ["ready"]
    assert _report(profiles=checks).status == "ready"


@pytest.mark.parametrize(
    ("readable", "digests", "expected"),
    [
        (False, (), None),
        (True, (), set()),
        (True, ("sha256:" + "a" * 64,), {"sha256:" + "a" * 64}),
    ],
)
def test_local_images_come_from_sandboxd_health(
    monkeypatch, readable, digests, expected
):
    """The unprivileged API must never need the Docker socket itself."""
    config = _SandboxConfig()
    health = type(
        "Health",
        (),
        {"image_store_readable": readable, "local_image_digests": digests},
    )()
    monkeypatch.setattr(
        "tga.sandbox.config.load_sandbox_config", lambda *a, **k: (config, None)
    )
    monkeypatch.setattr(readiness, "_sandboxd_health", lambda value: health)

    assert readiness._local_image_digests() == expected
