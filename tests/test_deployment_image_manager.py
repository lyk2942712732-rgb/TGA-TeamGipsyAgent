"""A deployment must not claim it prepared images it never looked at."""

from __future__ import annotations

import ast
import io
import subprocess
import threading

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
    behaviour = {"present": False, "pull_ok": True, "pull_stderr": "", "pull_output": []}

    def fake_run(args, **kwargs):
        calls.append(list(args))
        verb = args[1]
        if verb == "image":
            code = 0 if behaviour["present"] else 1
            return subprocess.CompletedProcess(args, code, "", "No such image")
        raise AssertionError(f"unexpected docker verb {verb} on subprocess.run")

    def fake_popen(args, **kwargs):
        # The pull streams, so it is a Popen rather than a run; docker's two
        # streams are merged, which is what the production call asks for.
        assert kwargs.get("stderr") is subprocess.STDOUT, "the reason must not be lost"
        calls.append(list(args))
        assert args[1] == "pull", f"unexpected docker verb {args[1]} on Popen"
        if behaviour["pull_ok"]:
            behaviour["present"] = True
            return _FakeProcess(behaviour["pull_output"], 0)
        return _FakeProcess([*behaviour["pull_output"], behaviour["pull_stderr"]], 1)

    monkeypatch.setattr(image_manager.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(image_manager.subprocess, "run", fake_run)
    monkeypatch.setattr(image_manager.subprocess, "Popen", fake_popen)
    return type("Docker", (), {"calls": calls, "behaviour": behaviour})()


class _FakeProcess:
    """Just enough of Popen for a streamed pull."""

    def __init__(self, lines, returncode):
        self.stdout = io.StringIO("".join(f"{line}\n" for line in lines))
        self._final = returncode
        self.returncode = None

    def wait(self):
        if self.returncode is None:
            self.returncode = self._final
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_profiles_without_an_image_of_their_own_are_not_wanted():
    config = _config(id="remote-http", provider="remote_http", image=None, toolset_digest=None)
    assert image_manager.wanted_images(config) == []


def test_shared_universal_image_is_wanted_only_once():
    config = _config()
    first = config.profiles["ctf-web-v1"]
    config.profiles["static-analysis-v1"] = first.model_copy(
        update={"id": "static-analysis-v1"}
    )

    assert image_manager.wanted_images(config) == [("ctf-web-v1", PINNED)]


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


class _StalledProcess:
    """A pull that produces nothing until it is killed.

    This is the case the timeout exists for, and the one a deadline checked
    between output lines would never notice: a stalled pull emits no lines.
    """

    def __init__(self):
        self.killed = threading.Event()
        self.returncode = None
        self.stdout = self

    def __iter__(self):
        return self

    def __next__(self):
        self.killed.wait(timeout=10)
        raise StopIteration

    def close(self):
        pass

    def kill(self):
        self.returncode = -9
        self.killed.set()

    def wait(self):
        return self.returncode


def test_a_stalled_pull_is_interrupted_rather_than_waited_on(monkeypatch):
    """Sandbox availability is graded, so a stuck registry degrades, not fails."""
    stalled = _StalledProcess()

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(image_manager.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(image_manager.subprocess, "run", fake_run)
    monkeypatch.setattr(image_manager.subprocess, "Popen", lambda *a, **k: stalled)

    report = image_manager.ensure_images(_config(), pull=True, timeout_seconds=0.1)

    assert stalled.killed.is_set(), "the watchdog never interrupted the blocked read"
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


# A first install fetching tens of gigabytes printed nothing at all until the
# whole of `tga up` had finished: not the image being fetched, not a rate, and
# not the reason a pull failed.  Silence for that long is indistinguishable
# from a hang, and the operator has no way to tell which image is the problem.


def _lines(docker, **kwargs) -> list[str]:
    said: list[str] = []
    image_manager.ensure_images(_config(), pull=True, progress=said.append, **kwargs)
    return said


def test_each_image_is_named_before_it_is_fetched(docker):
    said = _lines(docker)

    assert any("pulling" in line and "ctf-web" in line for line in said), said
    assert any(line.startswith("  [1/1]") for line in said), "no position in the queue"


def test_a_finished_pull_reports_how_long_it_took(docker):
    said = _lines(docker)

    assert any("pulled in" in line for line in said), said


def test_a_failed_pull_names_the_image_and_the_reason_as_it_happens(docker):
    """A large universal image still needs to report a pull failure immediately."""
    docker.behaviour["pull_ok"] = False
    docker.behaviour["pull_stderr"] = "denied: requested access to the resource is denied"

    said = _lines(docker)

    failure = [line for line in said if "FAILED" in line]
    assert failure, said
    assert "ctf-web" in failure[0]
    assert "denied" in failure[0]


def test_dockers_own_progress_is_forwarded_but_not_flooded(docker):
    """One line per layer transition is noise; the first is news."""
    docker.behaviour["pull_output"] = [f"{i:012x}: Downloading" for i in range(200)]

    said = _lines(docker)

    forwarded = [line for line in said if "Downloading" in line]
    assert forwarded, "docker's own output never reached the operator"
    assert len(forwarded) < 10, f"forwarded {len(forwarded)} lines of 200; not throttled"


def test_an_image_already_present_is_still_accounted_for(docker):
    """Twenty-one silent skips and one pull looks like nineteen lost images."""
    docker.behaviour["present"] = True

    said = _lines(docker)

    assert any("already present" in line for line in said), said


def test_a_plain_availability_check_says_nothing(docker):
    """Only a pull narrates; `tga up` without --pull-images stays quiet."""
    said: list[str] = []

    image_manager.ensure_images(_config(), pull=False, progress=said.append)

    assert said == []
