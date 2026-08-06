"""A script with a shebang that is not executable fails only where it runs."""

from __future__ import annotations

import subprocess

from tga.deployment.paths import project_root

ROOT = project_root()


def _tracked_shell_scripts() -> dict[str, str]:
    """Map every tracked *.sh to its git mode, which is what a clone gets."""
    listing = subprocess.run(
        ["git", "ls-files", "-s", "*.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    modes: dict[str, str] = {}
    for line in listing.stdout.splitlines():
        fields = line.split(maxsplit=3)
        if len(fields) == 4:
            modes[fields[3].strip()] = fields[0]
    return modes


def test_every_shell_script_meant_to_be_run_is_executable():
    """The mode in the index is what a fresh clone gets, on every platform.

    `provision.sh` and `install.sh` were committed 0644. Nothing noticed for
    a long time because install.sh invokes provision.sh as `bash …`, which
    ignores the bit -- but the rootfs image build runs it directly and stopped
    with `Permission denied`, and a user typing `./install.sh` would have hit
    exactly the same wall.
    """
    modes = _tracked_shell_scripts()
    if not modes:
        import pytest

        pytest.skip("not a git checkout")

    non_executable = sorted(path for path, mode in modes.items() if mode != "100755")
    assert non_executable == [], (
        "these carry a shebang but not the executable bit: " + ", ".join(non_executable)
    )


def test_the_scripts_that_matter_are_actually_tracked():
    """Guard against the check above passing because it found nothing."""
    modes = _tracked_shell_scripts()
    if not modes:
        import pytest

        pytest.skip("not a git checkout")

    for expected in (
        "deploy/wsl-rootfs/provision.sh",
        "deploy/linux-package/install.sh",
    ):
        assert expected in modes, f"{expected} is not tracked"
