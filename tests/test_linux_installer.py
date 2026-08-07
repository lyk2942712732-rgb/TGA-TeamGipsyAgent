"""Linux package scripts are contracts, including their failure paths."""

from __future__ import annotations

import re

from tga.deployment.paths import project_root


INSTALLER = project_root() / "deploy" / "linux-package" / "install.sh"
PROVISION = project_root() / "deploy" / "wsl-rootfs" / "provision.sh"
API_UNIT = project_root() / "deploy" / "systemd" / "tga-api.service"


def test_source_package_directory_is_not_a_compiled_launcher():
    body = INSTALLER.read_text(encoding="utf-8")
    assert 'if [ -f "$SOURCE_DIR/tga" ] && [ -x "$SOURCE_DIR/tga" ]; then' in body


def test_sandboxd_build_is_checked_and_atomically_installed():
    body = PROVISION.read_text(encoding="utf-8")
    function = re.search(r"^install_sandboxd\(\) \{\n(.*?)^\}", body, re.M | re.S)
    assert function
    implementation = function.group(1)

    assert "GOTOOLCHAIN=local" in implementation
    assert "requires Go $required_go or newer" in implementation
    assert "if ! ( cd \"$source\"" in implementation
    assert 'mktemp "$TGA_PREFIX/bin/.tga-sandboxd.XXXXXX"' in implementation
    assert 'if ! mv -f "$staged" "$target"' in implementation
    assert implementation.rstrip().endswith("return 0")


def test_installed_user_gets_scoped_management_not_docker_membership():
    body = PROVISION.read_text(encoding="utf-8")
    assert 'TGA_ADMIN_GROUP="${TGA_ADMIN_GROUP:-tga-admin}"' in body
    assert 'setfacl -m "u:$TGA_ADMIN_USER:rwx"' in body
    assert "%$TGA_ADMIN_GROUP ALL=(root) NOPASSWD:" in body
    assert '"$TGA_ADMIN_USER" "$SYSTEMCTL_PATH" "$command"' in body
    assert 'usermod -a -G "$TGA_ADMIN_GROUP" "$TGA_ADMIN_USER"' in body
    assert 'usermod -a -G "docker"' not in body
    assert 'install -d -m 2770' in body
    assert 'install -d -m 0777' not in body


def test_systemd_unit_uses_safe_defaults_and_runtime_override():
    body = API_UNIT.read_text(encoding="utf-8")
    assert "Environment=TGA_API_HOST=127.0.0.1" in body
    assert "Environment=TGA_API_PORT=8123" in body
    assert "EnvironmentFile=-/var/lib/tga/state/tga-api.env" in body
    assert "serve --host ${TGA_API_HOST} --port ${TGA_API_PORT}" in body
