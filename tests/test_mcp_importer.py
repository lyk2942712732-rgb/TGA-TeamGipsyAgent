from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from tga.tools.mcp_config import delete_mcp_server, load_mcp_config, set_mcp_server_enabled
from tga.tools.mcp_importer import CommandResult, MCPImageImporter, MCPImportError


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "mcp.json"
    path.write_text('{"version":1,"maxConcurrency":4,"servers":{}}', encoding="utf-8")
    return path


def test_source_archive_is_rejected_without_running_docker(tmp_path: Path) -> None:
    package = tmp_path / "demo-service.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("demo/Dockerfile", "FROM scratch\n")
        archive.writestr("demo/server.py", "print('mcp')\n")
    commands: list[list[str]] = []

    def run(command: list[str], cwd: Path | None, timeout: int) -> CommandResult:
        commands.append(command)
        if command[1:3] == ["image", "inspect"]:
            return CommandResult(returncode=0, stdout="sha256:abc\n")
        return CommandResult(returncode=0, stdout="build complete\n")

    with pytest.raises(MCPImportError, match="docker save"):
        MCPImageImporter(config_path=_config(tmp_path), command_runner=run).import_package(package, package.name)
    assert commands == []


def test_docker_archive_loads_one_image_and_updates_config(tmp_path: Path) -> None:
    package = tmp_path / "scanner.tar"
    payload = b"[]"
    with tarfile.open(package, "w") as archive:
        member = tarfile.TarInfo("manifest.json")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    def run(command: list[str], cwd: Path | None, timeout: int) -> CommandResult:
        if command[1] == "load":
            return CommandResult(returncode=0, stdout="Loaded image: registry.local/scanner-mcp:v1\n")
        return CommandResult(returncode=0, stdout="sha256:def\n")

    result = MCPImageImporter(config_path=_config(tmp_path), command_runner=run).import_package(package, package.name)

    assert result.source_type == "docker-image"
    assert result.image == "registry.local/scanner-mcp:v1"
    assert result.server_id == "scanner"
    config = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    assert config["servers"]["scanner"]["stdio"]["image"] == result.image


def test_source_archive_rejects_zip_before_docker_runs(tmp_path: Path) -> None:
    package = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("../Dockerfile", "FROM scratch\n")
    called = False

    def run(command: list[str], cwd: Path | None, timeout: int) -> CommandResult:
        nonlocal called
        called = True
        return CommandResult(returncode=0)

    with pytest.raises(MCPImportError, match="docker save"):
        MCPImageImporter(config_path=_config(tmp_path), command_runner=run).import_package(package, package.name)
    assert called is False


def test_docker_archive_with_multiple_repo_tags_requires_selection(tmp_path: Path) -> None:
    package = tmp_path / "bundle.tar"
    payload = b"[]"
    with tarfile.open(package, "w") as archive:
        member = tarfile.TarInfo("manifest.json")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    def run(command: list[str], cwd: Path | None, timeout: int) -> CommandResult:
        if command[1] == "load":
            return CommandResult(returncode=0, stdout="Loaded image: one-mcp:v1\nLoaded image: two-mcp:v2\n")
        return CommandResult(returncode=0, stdout="sha256:present\n")

    config_path = _config(tmp_path)
    result = MCPImageImporter(config_path=config_path, command_runner=run).import_package(package, package.name)

    assert result.requires_selection is True
    assert result.images == ["one-mcp:v1", "two-mcp:v2"]
    assert result.server_id == ""
    config, _ = load_mcp_config(config_path)
    assert config.servers == {}


def test_delete_server_removes_only_the_config_entry(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"version": 1, "servers": {"demo": {"command": "docker", "args": ["run", "--rm", "-i", "demo-mcp:latest"]}}}),
        encoding="utf-8",
    )

    assert set_mcp_server_enabled(path, "demo", False) is False
    config, _ = load_mcp_config(path)
    assert config.servers["demo"].enabled is False
    assert set_mcp_server_enabled(path, "demo", True) is True
    config, _ = load_mcp_config(path)
    assert config.servers["demo"].enabled is True

    assert delete_mcp_server(path, "demo") is True
    config, _ = load_mcp_config(path)
    assert config.servers == {}
    assert delete_mcp_server(path, "demo") is False
    with pytest.raises(KeyError):
        set_mcp_server_enabled(path, "demo", True)
