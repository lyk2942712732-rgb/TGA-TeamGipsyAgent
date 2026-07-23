"""Operator-initiated import of local MCP Docker images and build contexts.

The browser supplies only file bytes and an original filename. Docker commands,
tags and the resulting mcp.json entry are derived entirely by the host.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field

from tga.tools.mcp_config import DockerSecurityConfig, MCPConfig, MCPServerConfig, MCPStdioConfig, mutate_mcp_config


DEFAULT_MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_FILES = 20_000
_IMPORT_LOCK = threading.RLock()
_IMAGE_LINE_RE = re.compile(r"^Loaded image:\s*(.+?)\s*$", re.MULTILINE)
_IMAGE_ID_RE = re.compile(r"^Loaded image ID:\s*(sha256:[a-f0-9]+)\s*$", re.MULTILINE)


class MCPImportError(RuntimeError):
    def __init__(self, message: str, *, code: str = "IMPORT_FAILED") -> None:
        super().__init__(message)
        self.code = code


class MCPImportResult(BaseModel):
    server_id: str = ""
    image: str = ""
    images: list[str] = Field(default_factory=list)
    requires_selection: bool = False
    source_type: str
    config_path: str
    config_action: str
    build_log: str = ""
    catalog: dict[str, Any] | None = None


class CommandResult(BaseModel):
    returncode: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[[list[str], Path | None, int], CommandResult]


class MCPImageImporter:
    """Build/load one user-selected package and atomically allowlist its image."""

    def __init__(
        self,
        *,
        config_path: str | Path,
        docker_executable: str = "docker",
        command_runner: CommandRunner | None = None,
        build_timeout_seconds: int = 1800,
        max_package_bytes: int = DEFAULT_MAX_PACKAGE_BYTES,
        max_extracted_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES,
        max_archive_files: int = DEFAULT_MAX_ARCHIVE_FILES,
    ) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.docker_executable = docker_executable
        self.command_runner = command_runner or self._run_command
        self.build_timeout_seconds = build_timeout_seconds
        self.max_package_bytes = max_package_bytes
        self.max_extracted_bytes = max_extracted_bytes
        self.max_archive_files = max_archive_files

    def import_package(self, package_path: str | Path, original_name: str) -> MCPImportResult:
        package = Path(package_path).resolve()
        self._validate_package(package, original_name)
        digest = _sha256_file(package)
        with _IMPORT_LOCK:
            source_type, images, log = self._materialize_image(package, original_name, digest)
            if len(images) == 1:
                image = images[0]
                server_id, action = self._upsert_config(image, original_name, digest)
            else:
                image, server_id, action = "", "", "selection_required"
        return MCPImportResult(
            server_id=server_id,
            image=image,
            images=images,
            requires_selection=len(images) > 1,
            source_type=source_type,
            config_path=str(self.config_path),
            config_action=action,
            build_log=_tail(log),
        )

    def _validate_package(self, package: Path, original_name: str) -> None:
        if not original_name or Path(original_name).name != original_name or "\x00" in original_name:
            raise MCPImportError("invalid upload filename", code="INVALID_PACKAGE")
        if not package.is_file():
            raise MCPImportError("uploaded MCP package does not exist", code="INVALID_PACKAGE")
        size = package.stat().st_size
        if size <= 0:
            raise MCPImportError("uploaded MCP package is empty", code="INVALID_PACKAGE")
        if size > self.max_package_bytes:
            raise MCPImportError(
                f"MCP package exceeds the {self.max_package_bytes} byte limit",
                code="PACKAGE_TOO_LARGE",
            )

    def _materialize_image(
        self,
        package: Path,
        original_name: str,
        digest: str,
    ) -> tuple[str, list[str], str]:
        lowered = original_name.casefold()
        if not lowered.endswith((".tar", ".tar.gz", ".tgz")):
            raise MCPImportError(
                "unsupported file; upload a Docker image archive created by docker save (.tar/.tar.gz/.tgz)",
                code="UNSUPPORTED_PACKAGE",
            )
        if not tarfile.is_tarfile(package):
            raise MCPImportError(
                "uploaded file is not a valid Docker image tar archive",
                code="UNSUPPORTED_PACKAGE",
            )
        with tarfile.open(package, mode="r:*") as archive:
            names = {_archive_path(member.name).as_posix() for member in archive.getmembers()}
        if "manifest.json" in names:
            return self._load_image(package, original_name, digest)
        raise MCPImportError(
            "archive is not a docker save image (manifest.json is missing); source archives and Dockerfiles are not accepted",
            code="UNSUPPORTED_PACKAGE",
        )

    def _load_image(self, package: Path, original_name: str, digest: str) -> tuple[str, list[str], str]:
        result = self.command_runner(
            [self.docker_executable, "load", "--input", str(package)],
            None,
            self.build_timeout_seconds,
        )
        combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
        if result.returncode != 0:
            raise MCPImportError(f"docker load failed: {_tail(combined)}", code="DOCKER_LOAD_FAILED")
        images = list(dict.fromkeys(match.strip() for match in _IMAGE_LINE_RE.findall(combined) if match.strip()))
        if images:
            for image in images:
                self._inspect_image(image)
            return "docker-image", images, combined
        else:
            ids = list(dict.fromkeys(_IMAGE_ID_RE.findall(combined)))
            if len(ids) != 1:
                raise MCPImportError("docker load did not report one importable image", code="IMAGE_NOT_FOUND")
            image = _generated_image_name(original_name, digest)
            tagged = self.command_runner([self.docker_executable, "tag", ids[0], image], None, 60)
            if tagged.returncode != 0:
                raise MCPImportError(f"docker tag failed: {_tail(tagged.stderr)}", code="DOCKER_TAG_FAILED")
        self._inspect_image(image)
        return "docker-image", [image], combined

    def _build_source(self, source_root: Path, original_name: str, digest: str) -> tuple[str, str, str]:
        dockerfiles = [path for path in source_root.rglob("Dockerfile") if path.is_file()]
        if len(dockerfiles) != 1:
            raise MCPImportError(
                f"source archive must contain exactly one Dockerfile; found {len(dockerfiles)}",
                code="INVALID_BUILD_CONTEXT",
            )
        dockerfile = dockerfiles[0]
        context = dockerfile.parent
        image = _generated_image_name(original_name, digest)
        result = self.command_runner(
            [
                self.docker_executable,
                "build",
                "--pull=false",
                "--tag",
                image,
                "--file",
                str(dockerfile),
                str(context),
            ],
            context,
            self.build_timeout_seconds,
        )
        combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
        if result.returncode != 0:
            raise MCPImportError(f"docker build failed: {_tail(combined)}", code="DOCKER_BUILD_FAILED")
        self._inspect_image(image)
        return "docker-build", image, combined

    def _inspect_image(self, image: str) -> None:
        result = self.command_runner(
            [self.docker_executable, "image", "inspect", image, "--format", "{{.Id}}"],
            None,
            60,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise MCPImportError(f"built image cannot be inspected: {_tail(result.stderr)}", code="IMAGE_NOT_FOUND")

    def _upsert_config(self, image: str, original_name: str, digest: str) -> tuple[str, str]:
        server_id = _server_id(image, original_name)
        action = "created"

        def update(config: MCPConfig) -> MCPConfig:
            nonlocal server_id, action
            base_id = server_id
            existing = config.servers.get(server_id)
            if existing is not None and _configured_image(existing) != image:
                server_id = f"{base_id}-{digest[:8]}"
                existing = config.servers.get(server_id)
            if existing is not None:
                action = "updated"
            payload = config.model_dump(mode="json", by_alias=True)
            payload["servers"][server_id] = default_docker_server(image).model_dump(mode="json", by_alias=True)
            return MCPConfig.model_validate(payload)

        mutate_mcp_config(self.config_path, update)
        return server_id, action

    def _extract_zip(self, package: Path, destination: Path) -> None:
        total = 0
        with zipfile.ZipFile(package) as archive:
            entries = archive.infolist()
            self._check_entry_count(entries)
            for entry in entries:
                relative = _archive_path(entry.filename)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise MCPImportError("source archive may not contain symbolic links", code="UNSAFE_ARCHIVE")
                target = destination.joinpath(*relative.parts)
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total = self._checked_total(total, entry.file_size)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)

    def _extract_tar(self, package: Path, destination: Path) -> None:
        total = 0
        with tarfile.open(package, mode="r:*") as archive:
            entries = archive.getmembers()
            self._check_entry_count(entries)
            for entry in entries:
                relative = _archive_path(entry.name)
                if entry.issym() or entry.islnk() or entry.isdev():
                    raise MCPImportError("source archive may not contain links or device files", code="UNSAFE_ARCHIVE")
                target = destination.joinpath(*relative.parts)
                if entry.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not entry.isfile():
                    raise MCPImportError("source archive contains an unsupported entry", code="UNSAFE_ARCHIVE")
                total = self._checked_total(total, entry.size)
                source = archive.extractfile(entry)
                if source is None:
                    raise MCPImportError("could not read source archive entry", code="INVALID_PACKAGE")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)

    def _check_entry_count(self, entries: list[Any]) -> None:
        if len(entries) > self.max_archive_files:
            raise MCPImportError(
                f"source archive contains more than {self.max_archive_files} entries",
                code="ARCHIVE_TOO_LARGE",
            )

    def _checked_total(self, current: int, size: int) -> int:
        total = current + max(0, size)
        if total > self.max_extracted_bytes:
            raise MCPImportError(
                f"source archive expands beyond {self.max_extracted_bytes} bytes",
                code="ARCHIVE_TOO_LARGE",
            )
        return total

    @staticmethod
    def _run_command(command: list[str], cwd: Path | None, timeout: int) -> CommandResult:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise MCPImportError("Docker CLI is not installed or is not on PATH", code="DOCKER_UNAVAILABLE") from exc
        except subprocess.TimeoutExpired as exc:
            output = "\n".join(str(value or "") for value in (exc.stdout, exc.stderr))
            raise MCPImportError(f"Docker command timed out: {_tail(output)}", code="DOCKER_TIMEOUT") from exc
        return CommandResult(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def default_docker_server(image: str) -> MCPServerConfig:
    """Return the fixed host policy used for images explicitly imported by an operator."""
    return MCPServerConfig(
        enabled=False,
        transport="stdio",
        stdio=MCPStdioConfig(
            source="docker_image",
            image=image,
            docker=DockerSecurityConfig(
                memory="1g",
                cpus=1.0,
                pids_limit=256,
                network="bridge",
                read_only=True,
                cap_drop_all=True,
                no_new_privileges=True,
            ),
        ),
        timeout_seconds=60,
        tool_timeout_seconds=600,
    )


def _archive_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or path.is_absolute() or ".." in path.parts:
        raise MCPImportError("source archive contains an unsafe path", code="UNSAFE_ARCHIVE")
    if path.parts and re.fullmatch(r"[A-Za-z]:", path.parts[0]):
        raise MCPImportError("source archive contains an absolute Windows path", code="UNSAFE_ARCHIVE")
    return path


def _generated_image_name(original_name: str, digest: str) -> str:
    stem = original_name.casefold()
    for suffix in (".tar.gz", ".tar", ".tgz", ".zip", ".oci"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")[:48] or "service"
    return f"tga-mcp-{slug}:{digest[:12]}"


def _server_id(image: str, original_name: str) -> str:
    repository = image.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0]
    repository = repository.removesuffix("-mcp")
    fallback = Path(original_name).stem
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", repository or fallback).strip("-_")[:56]
    return value or "imported-mcp"


def _configured_image(server: MCPServerConfig) -> str | None:
    return server.stdio.image if server.stdio and server.stdio.source == "docker_image" else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tail(value: str, limit: int = 12_000) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[-limit:]
