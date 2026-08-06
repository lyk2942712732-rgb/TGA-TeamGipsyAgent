"""What provisioning downloads has to be pinned, and refusals must abort.

These read the installer as text on purpose. The behaviour they protect is not
reachable from Python -- it lives in a shell script that runs once, as root, on
a machine nobody is watching.
"""

from __future__ import annotations

import re

from tga.deployment.paths import project_root

PROVISION = (project_root() / "deploy" / "wsl-rootfs" / "provision.sh").read_text(
    encoding="utf-8"
)


def _function(name: str) -> str:
    body = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}$", PROVISION, re.M | re.S)
    assert body, f"{name} is missing from provision.sh"
    return body.group(1)


def test_gvisor_is_pinned_to_a_dated_release():
    """`latest` moves, and a checksum pinned against a moving target is a lie."""
    release = re.search(r'^GVISOR_RELEASE="\$\{GVISOR_RELEASE:-(\d{8})\}"', PROVISION, re.M)
    assert release, "GVISOR_RELEASE must default to a dated release"
    assert "release/latest" not in PROVISION


def test_every_supported_architecture_carries_a_checksum():
    for arch in ("x86_64", "aarch64"):
        digest = re.search(rf"^GVISOR_SHA512_{arch}=([0-9a-f]+)$", PROVISION, re.M)
        assert digest, f"no pinned checksum for {arch}"
        assert len(digest.group(1)) == 128, f"{arch} checksum is not a sha512"


def test_the_docker_repository_key_is_pinned():
    fingerprint = re.search(
        r'^DOCKER_KEY_FINGERPRINT="\$\{DOCKER_KEY_FINGERPRINT:-([0-9A-F]{40})\}"',
        PROVISION,
        re.M,
    )
    assert fingerprint, "the Docker signing key fingerprint must be pinned"


def test_a_wrong_key_or_checksum_aborts_rather_than_warning():
    """Installing anyway would make the check decorative.

    `fail` exits; `log` prints and carries on. Both refusals must use `fail`.
    """
    docker = _function("install_docker")
    mismatch = re.search(r'if \[ "\$measured" != "\$DOCKER_KEY_FINGERPRINT" \]; then(.*?)fi', docker, re.S)
    assert mismatch and "fail " in mismatch.group(1), "a wrong signing key must abort"

    runsc = _function("install_runsc")
    checksum = re.search(r"if ! printf .*?sha512sum.*?then(.*?)fi", runsc, re.S)
    assert checksum and "fail " in checksum.group(1), "a wrong checksum must abort"


def test_each_installer_step_is_checked_explicitly():
    """`f || log ...` disables set -e for everything f calls.

    Both installers are invoked that way, so an unchecked failure would fall
    through -- and the first version of this did exactly that, adding an apt
    repository whose key had failed to install and then running apt-get on it.
    """
    assert re.search(r"^\s*install_docker \|\| log ", PROVISION, re.M)
    assert re.search(r"^\s*install_runsc \|\| log ", PROVISION, re.M)

    # Comments in this function quote the very commands under test, so they
    # have to go before matching or the prose satisfies the assertion.
    docker = "\n".join(
        line for line in _function("install_docker").splitlines()
        if not line.lstrip().startswith("#")
    )
    for command in ("curl -fsSL", "gpg --batch", "apt-get update", "apt-get install"):
        statement = re.search(
            rf"^\s*(if ! )?{re.escape(command)}\b[^\n]*(\n\s+[^\n]*)*", docker, re.M
        )
        assert statement, f"{command} is not called in install_docker"
        assert "||" in statement.group(0) or statement.group(1), (
            f"{command} in install_docker is unchecked:\n{statement.group(0)}"
        )


def test_runsc_is_registered_with_docker_and_the_result_is_verified():
    """Installing the binary is not the same as Docker knowing about it."""
    assert "runsc install" in _function("install_runsc")
    assert "docker info --format '{{json .Runtimes}}'" in PROVISION
    assert "does not report a runsc runtime" in PROVISION


def test_installing_the_engine_can_be_declined():
    """An operator who already manages Docker must not get a second opinion."""
    for switch in ("TGA_INSTALL_DOCKER", "TGA_INSTALL_RUNSC"):
        assert re.search(rf'^{switch}="\$\{{{switch}:-1\}}"', PROVISION, re.M)
    assert 'if command -v docker >/dev/null 2>&1; then' in _function("install_docker")
    assert 'if command -v runsc >/dev/null 2>&1; then' in _function("install_runsc")
