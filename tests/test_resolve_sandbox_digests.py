"""Pinning sandbox.json must never accept a reference it cannot verify."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import resolve_sandbox_digests as resolver  # noqa: E402

REAL = "sha256:" + "a" * 64


def _config(**overrides) -> dict:
    payload = {
        "runtime": "enforced",
        "docker_sandbox": {"template": f"docker.io/docker/sandbox-templates:shell-docker@{REAL}"},
        "profiles": {
            "ctf-web-v1": {"id": "ctf-web-v1", "provider": "sandboxd",
                           "image": f"ghcr.io/owner/tga-kali-ctf-web@{REAL}"},
            "remote-http": {"id": "remote-http", "provider": "remote_http"},
        },
    }
    payload.update(overrides)
    return payload


def test_fully_pinned_config_reports_nothing_unresolved():
    assert resolver.unresolved(_config()) == []


def test_placeholder_image_is_reported():
    config = _config()
    config["profiles"]["ctf-web-v1"]["image"] = (
        "ghcr.io/owner/tga-kali-ctf-web@sha256:REPLACE_WITH_RELEASE_DIGEST"
    )
    assert "ctf-web-v1" in resolver.unresolved(config)


def test_mutable_tag_is_reported():
    """A tag can be repointed, so it verifies nothing."""
    config = _config()
    config["profiles"]["ctf-web-v1"]["image"] = "ghcr.io/owner/tga-kali-ctf-web:latest"
    assert "ctf-web-v1" in resolver.unresolved(config)


def test_unpinned_template_is_reported():
    config = _config()
    config["docker_sandbox"]["template"] = "docker.io/docker/sandbox-templates:shell-docker"
    assert "docker_sandbox.template" in resolver.unresolved(config)


def test_remote_http_profiles_need_no_image():
    config = _config()
    assert "remote-http" not in resolver.unresolved(config)


def test_apply_published_pins_every_matching_profile(tmp_path):
    """The release listing is the authority the workflow already produces."""
    targets = resolver.load_matrix()
    listing = tmp_path / "published-images.txt"
    listing.write_text(
        "\n".join(
            f"ghcr.io/owner/{target.image}@sha256:{index:064x}"
            for index, target in enumerate(targets, start=1)
        )
        + "\n",
        encoding="utf-8",
    )
    config = {"profiles": {t.profile_id: {"id": t.profile_id, "provider": "sandboxd"}
                           for t in targets}}

    changed, problems = resolver.apply_published(config, listing)
    assert problems == []
    assert changed == len(targets)
    for index, target in enumerate(targets, start=1):
        assert config["profiles"][target.profile_id]["image"] == (
            f"ghcr.io/owner/{target.image}@sha256:{index:064x}"
        )


def test_apply_published_ignores_the_base_image(tmp_path):
    """tga-kali-base backs the others; it is not itself a profile."""
    listing = tmp_path / "published-images.txt"
    listing.write_text(f"ghcr.io/owner/{resolver.BASE_IMAGE_NAME}@{REAL}\n", encoding="utf-8")

    changed, problems = resolver.apply_published({"profiles": {}}, listing)
    assert changed == 0
    assert problems == []


def test_apply_published_refuses_an_unpinned_reference(tmp_path):
    listing = tmp_path / "published-images.txt"
    listing.write_text("ghcr.io/owner/tga-kali-ctf-web:release\n", encoding="utf-8")

    changed, problems = resolver.apply_published(_config(), listing)
    assert changed == 0
    assert any("not digest-pinned" in problem for problem in problems)


def test_apply_published_reports_an_unknown_image(tmp_path):
    listing = tmp_path / "published-images.txt"
    listing.write_text(f"ghcr.io/owner/tga-kali-not-a-solver@{REAL}\n", encoding="utf-8")

    changed, problems = resolver.apply_published(_config(), listing)
    assert changed == 0
    assert any("does not map to any profile" in problem for problem in problems)


def test_matrix_rows_match_the_shipped_profiles():
    """Every buildable image must correspond to a configured profile."""
    from tga.deployment.paths import project_root

    config = json.loads(
        (project_root() / "config" / "sandbox.json").read_text(encoding="utf-8")
    )
    for target in resolver.load_matrix():
        assert target.profile_id in config["profiles"], target.profile_id


def test_solver_dockerfiles_carry_no_placeholder_base():
    """A placeholder BASE_IMAGE default cannot be built by anyone."""
    from tga.deployment.paths import project_root

    solvers = project_root() / "containers" / "kali" / "solvers"
    offenders = [
        path.parent.name
        for path in sorted(solvers.glob("*/Dockerfile"))
        if resolver.PLACEHOLDER in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


@pytest.mark.parametrize("reference", [
    "ghcr.io/owner/img@sha256:" + "a" * 64,
    "localhost:5000/img@sha256:" + "f" * 64,
])
def test_digest_pattern_accepts_valid_references(reference):
    assert resolver.DIGEST_RE.search(reference)


@pytest.mark.parametrize("reference", [
    "ghcr.io/owner/img:latest",
    "ghcr.io/owner/img@sha256:REPLACE_WITH_RELEASE_DIGEST",
    "ghcr.io/owner/img@sha256:tooshort",
    "ghcr.io/owner/img@sha1:" + "a" * 40,
])
def test_digest_pattern_rejects_unverifiable_references(reference):
    assert not resolver.DIGEST_RE.search(reference)
