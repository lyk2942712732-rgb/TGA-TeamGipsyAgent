"""Generated sandbox configuration must never claim unenforceable isolation."""

from __future__ import annotations

import json

import pytest

from tga.deployment import config_generator

PINNED = "ghcr.io/team-gipsy/tga-kali-ctf-web@sha256:" + "a" * 64
PLACEHOLDER = "ghcr.io/team-gipsy/tga-kali-ctf-web@sha256:REPLACE_WITH_RELEASE_DIGEST"


def _config(**overrides) -> dict:
    payload = {
        "version": 1,
        "runtime": "enforced",
        "docker_sandbox": {"executable": "sbx", "task_root": "runs",
                           "template": "docker.io/x/y@sha256:" + "b" * 64},
        "sandboxd": {"socket_path": "/run/tga-sandboxd/sandboxd.sock",
                     "run_root": "/var/lib/tga/runs", "allowed_client_uids": [1001]},
        "profiles": {
            "ctf-web-v1": {"id": "ctf-web-v1", "provider": "sandboxd",
                           "image": PINNED, "toolset_digest": "c" * 64},
        },
    }
    payload.update(overrides)
    return payload


def test_a_fully_pinned_enforced_configuration_validates():
    assert config_generator.validate(_config()).ok


def test_empty_client_uids_are_rejected_under_enforcement():
    """sandboxd would reject every caller, so startup must not claim success."""
    payload = _config()
    payload["sandboxd"]["allowed_client_uids"] = []
    report = config_generator.validate(payload)
    assert not report.ok
    assert any("allowed_client_uids" in error for error in report.errors)


def test_placeholder_digest_is_rejected_under_enforcement():
    payload = _config()
    payload["profiles"]["ctf-web-v1"]["image"] = PLACEHOLDER
    report = config_generator.validate(payload)
    assert not report.ok
    assert any("placeholder" in error for error in report.errors)


def test_mutable_tag_is_rejected_under_enforcement():
    payload = _config()
    payload["profiles"]["ctf-web-v1"]["image"] = "ghcr.io/team-gipsy/tga-kali-ctf-web:latest"
    report = config_generator.validate(payload)
    assert not report.ok
    assert any("digest-pinned" in error for error in report.errors)


def test_missing_toolset_digest_is_rejected_under_enforcement():
    payload = _config()
    del payload["profiles"]["ctf-web-v1"]["toolset_digest"]
    report = config_generator.validate(payload)
    assert not report.ok
    assert any("toolset digest" in error for error in report.errors)


def test_placeholders_only_warn_while_the_sandbox_is_disabled():
    """A disabled deployment is legitimate; it just must say so."""
    payload = _config(runtime="disabled")
    payload["profiles"]["ctf-web-v1"]["image"] = PLACEHOLDER
    report = config_generator.validate(payload)
    assert report.ok
    assert any("not 'enforced'" in warning for warning in report.warnings)


def test_remote_http_profiles_need_no_image():
    payload = _config()
    payload["profiles"]["remote-http"] = {"id": "remote-http", "provider": "remote_http"}
    assert config_generator.validate(payload).ok


def test_bind_to_host_writes_run_root_and_uids():
    bound = config_generator.bind_to_host(
        _config(), run_root="/var/lib/tga/runs", client_uids=(999, 999, 42)
    )
    assert bound["sandboxd"]["run_root"] == "/var/lib/tga/runs"
    assert bound["sandboxd"]["allowed_client_uids"] == [42, 999]
    assert bound["docker_sandbox"]["task_root"] == "/var/lib/tga/runs"


def test_bind_to_host_does_not_mutate_the_input():
    original = _config()
    config_generator.bind_to_host(original, run_root="/elsewhere", client_uids=(7,))
    assert original["sandboxd"]["run_root"] == "/var/lib/tga/runs"


def test_generate_writes_the_bound_configuration(tmp_path):
    path = tmp_path / "sandbox.json"
    path.write_text(json.dumps(_config()), encoding="utf-8")

    bound, report = config_generator.generate(
        path, run_root="/var/lib/tga/runs", client_uids=(1234,)
    )
    assert report.ok
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["sandboxd"]["allowed_client_uids"] == [1234]
    assert bound["sandboxd"]["run_root"] == "/var/lib/tga/runs"


def test_check_only_leaves_the_file_untouched(tmp_path):
    path = tmp_path / "sandbox.json"
    before = json.dumps(_config())
    path.write_text(before, encoding="utf-8")

    config_generator.generate(
        path, run_root="/changed", client_uids=(1,), write=False
    )
    assert path.read_text(encoding="utf-8") == before


def _shipped_config() -> dict:
    from tga.deployment.paths import project_root

    return json.loads(
        (project_root() / "config" / "sandbox.json").read_text(encoding="utf-8")
    )


def test_the_shipped_configuration_carries_no_placeholder_images():
    """A release must pin its digests back into the config it ships.

    sandbox-v0.1.0 published its images and left this file holding
    `REPLACE_WITH_RELEASE_DIGEST`, because the workflow only uploaded the
    rewritten copy as a build artifact. provision.sh seeds /etc/tga from here,
    so that shipped a config no host could ever enforce -- silently.
    """
    images = [
        profile.get("image") or ""
        for profile in _shipped_config()["profiles"].values()
    ]
    assert images, "expected the shipped config to declare profiles"
    assert [image for image in images if "REPLACE_WITH_RELEASE_DIGEST" in image] == []


def test_shipped_configuration_is_not_certified_until_bound_to_a_host():
    """`allowed_client_uids` is host-specific, so this file is still a template.

    Provisioning must say so rather than certify it, so nobody reads a green
    provision log as proof that tool execution is isolated.
    """
    report = config_generator.validate(_shipped_config())
    assert not report.ok
    assert any("allowed_client_uids" in error for error in report.errors)


def test_declared_enforcement_without_real_images_still_blocks_execution():
    """The safety property that actually matters.

    Load-time validation was deliberately relaxed so a half-provisioned host
    can still boot and report per-profile readiness. That is only acceptable
    because the execution boundary stays fail-closed: a profile whose digest
    is unresolved must be refused at use, not merely warned about.
    """
    from tga.sandbox.config import SandboxConfig
    from tga.sandbox.readiness import KaliProfileNotReadyError, ensure_kali_profile_ready

    # Injected rather than read out of the shipped file. Now that releases pin
    # their digests, the config carries no placeholder of its own, and a test
    # that waited for one to reappear would only fire once the bug it guards
    # against had already shipped.
    payload = _shipped_config()
    profile_id, profile = next(
        (profile_id, profile)
        for profile_id, profile in payload["profiles"].items()
        if profile.get("provider") != "remote_http"
    )
    profile["image"] = PLACEHOLDER
    config = SandboxConfig.model_validate(payload)

    with pytest.raises(KaliProfileNotReadyError) as excinfo:
        ensure_kali_profile_ready(profile_id, config)
    assert excinfo.value.reason == "unresolved_image_digest"
