"""Explicit, host-controlled MCP server configuration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator
from tga.modes import TASK_MODES, TaskMode, normalize_modes


SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "mcp.json"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[2] / "runs" / "mcp-cache.json"
_CONFIG_WRITE_LOCK = threading.RLock()


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


class WorkspaceMountConfig(BaseModel):
    model_config = ConfigDict(alias_generator=lambda name: _camel(name), populate_by_name=True, extra="forbid")

    enabled: bool = False
    container_path: str = "/workspace"
    read_only: bool = True

    @field_validator("container_path")
    @classmethod
    def validate_container_path(cls, value: str) -> str:
        if not value.startswith("/") or "\x00" in value:
            raise ValueError("workspaceMount.containerPath must be an absolute container path")
        return value


class DockerSecurityConfig(BaseModel):
    """Safe docker-run defaults; every relaxation must be explicit in mcp.json."""

    model_config = ConfigDict(alias_generator=lambda name: _camel(name), populate_by_name=True, extra="forbid")

    memory: str | None = "512m"
    cpus: float | None = 1.0
    pids_limit: int | None = 256
    network: str = "none"
    read_only: bool = True
    cap_drop_all: bool = True
    cap_add: list[str] = Field(default_factory=list)
    no_new_privileges: bool = True
    tmpfs: dict[str, str] = Field(
        default_factory=lambda: {
            "/tmp": "rw,noexec,nosuid,size=64m,mode=1777",
            "/app/output": "rw,noexec,nosuid,size=128m,mode=1777",
        }
    )

    @field_validator("cpus")
    @classmethod
    def validate_cpus(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("docker.cpus must be positive")
        return value

    @field_validator("pids_limit")
    @classmethod
    def validate_pids(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("docker.pidsLimit must be positive")
        return value

    @field_validator("cap_add")
    @classmethod
    def validate_cap_add(cls, values: list[str]) -> list[str]:
        allowed = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}
        normalized = [value.strip().upper() for value in values]
        unsupported = sorted(set(normalized) - allowed)
        if unsupported:
            raise ValueError(f"docker.capAdd contains unsupported capabilities: {', '.join(unsupported)}")
        return normalized

    @field_validator("tmpfs")
    @classmethod
    def validate_tmpfs(cls, value: dict[str, str]) -> dict[str, str]:
        for mount, options in value.items():
            if not mount.startswith("/") or "\x00" in mount or "\x00" in options:
                raise ValueError("docker.tmpfs entries must use safe absolute container paths")
        return value


def _validate_environment(value: dict[str, str]) -> dict[str, str]:
    for key in value:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"invalid environment variable name: {key}")
    return value


def _validate_secret_refs(value: dict[str, str]) -> dict[str, str]:
    for target, reference in value.items():
        if not target or "\x00" in target:
            raise ValueError("secretRefs targets must be non-empty")
        if not re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", reference):
            raise ValueError("secretRefs values must use env:VARIABLE references")
    return value


class MCPStdioConfig(BaseModel):
    """STDIO launch source. Docker images and local processes are deliberately distinct."""

    model_config = ConfigDict(alias_generator=lambda name: _camel(name), populate_by_name=True, extra="forbid")

    source: Literal["docker_image", "local_process"]
    image: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    workspace_mount: WorkspaceMountConfig = Field(default_factory=WorkspaceMountConfig)
    docker: DockerSecurityConfig | None = None

    @field_validator("image", "command")
    @classmethod
    def validate_executable_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(character in value for character in ("\x00", "\n", "\r")):
            raise ValueError("image/command must be a non-empty value, not a shell command")
        return value

    @field_validator("args")
    @classmethod
    def validate_args(cls, values: list[str]) -> list[str]:
        if any("\x00" in value for value in values):
            raise ValueError("args may not contain NUL characters")
        return values

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_environment(value)

    @field_validator("secret_refs")
    @classmethod
    def validate_secret_refs(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_secret_refs(value)

    @model_validator(mode="after")
    def validate_source(self) -> "MCPStdioConfig":
        if self.source == "docker_image":
            if not self.image:
                raise ValueError("stdio.image is required for docker_image sources")
            if self.command is not None or self.args:
                raise ValueError("docker_image sources may not provide command or args")
            if self.docker is None:
                self.docker = DockerSecurityConfig()
        else:
            if not self.command:
                raise ValueError("stdio.command is required for local_process sources")
            if self.image is not None or self.docker is not None or self.workspace_mount.enabled:
                raise ValueError("local_process sources may not provide image, docker, or workspaceMount")
        return self


class MCPHTTPConfig(BaseModel):
    """Streamable HTTP endpoint configuration; credentials stay as host secret references."""

    model_config = ConfigDict(alias_generator=lambda name: _camel(name), populate_by_name=True, extra="forbid")

    url: str
    verify_tls: bool = True
    headers: dict[str, str] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    proxy_url: str | None = None
    allow_same_origin_redirects: bool = False
    max_retries: int = Field(default=1, ge=0, le=2)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("http.url must be an http(s) URL without embedded credentials")
        if parsed.fragment:
            raise ValueError("http.url may not contain a fragment")
        return value.strip()

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("http.proxyUrl must be an explicit http(s) URL without embedded credentials")
        return value.strip()

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        sensitive = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"}
        for name, content in value.items():
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or "\r" in content or "\n" in content:
                raise ValueError("http.headers contains an invalid header")
            if name.casefold() in sensitive:
                raise ValueError(f"sensitive header {name} must be configured through secretRefs")
        return value

    @field_validator("secret_refs")
    @classmethod
    def validate_secret_refs(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_secret_refs(value)


class MCPVisibilityConfig(BaseModel):
    model_config = ConfigDict(alias_generator=lambda name: _camel(name), populate_by_name=True, extra="forbid")

    modes: list[TaskMode] = Field(default_factory=lambda: list(TASK_MODES))
    risk: Literal["passive", "active", "destructive"] = "active"
    allow_methods: list[str] = Field(default_factory=list)
    deny_methods: list[str] = Field(default_factory=list)

    @field_validator("modes", mode="before")
    @classmethod
    def migrate_modes(cls, value: Any) -> list[TaskMode]:
        return normalize_modes(value)


class MCPMethodPolicyConfig(BaseModel):
    model_config = ConfigDict(alias_generator=lambda name: _camel(name), populate_by_name=True, extra="forbid")

    enabled: bool = True
    modes: list[TaskMode] | None = None
    risk: Literal["passive", "active", "destructive"] | None = None
    argument_schema: dict[str, Any] | None = None

    @field_validator("modes", mode="before")
    @classmethod
    def migrate_modes(cls, value: Any) -> list[TaskMode] | None:
        return None if value is None else normalize_modes(value)


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(alias_generator=lambda name: _camel(name), populate_by_name=True, extra="forbid")

    enabled: bool = True
    transport: Literal["stdio", "streamable_http"] = "stdio"
    stdio: MCPStdioConfig | None = None
    http: MCPHTTPConfig | None = None
    enabled_tools: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    tool_timeout_seconds: int = Field(default=300, ge=1, le=7200)
    max_output_bytes: int = Field(default=262_144, ge=1024, le=64 * 1024 * 1024)
    max_inline_chars: int = Field(default=32_000, ge=256, le=1_000_000)
    max_artifact_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=256 * 1024 * 1024)
    store_sensitive_artifact_values: bool = False
    visibility: MCPVisibilityConfig = Field(default_factory=MCPVisibilityConfig)
    methods: dict[str, MCPMethodPolicyConfig] = Field(default_factory=dict)
    max_concurrency: int = Field(default=1, ge=1, le=32)
    calls_per_minute: float = Field(default=60.0, gt=0, le=60_000)
    burst: int = Field(default=5, ge=1, le=1000)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_stdio(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "stdio" in value or value.get("transport", "stdio") != "stdio":
            return value
        payload = dict(value)
        command = payload.pop("command", None)
        args = list(payload.pop("args", []))
        environment = payload.pop("environment", {})
        workspace_mount = payload.pop("workspaceMount", payload.pop("workspace_mount", {}))
        docker = payload.pop("docker", None)
        if command is None:
            return payload
        is_docker = Path(str(command)).name.casefold() in {"docker", "docker.exe"}
        if is_docker:
            image, tmpfs = _legacy_docker_image_and_tmpfs(args)
            if docker is None:
                docker = {}
            else:
                docker = dict(docker)
            if tmpfs and "tmpfs" not in docker:
                docker["tmpfs"] = tmpfs
            payload["stdio"] = {
                "source": "docker_image",
                "image": image,
                "environment": environment,
                "workspaceMount": workspace_mount,
                "docker": docker,
            }
        else:
            payload["stdio"] = {
                "source": "local_process",
                "command": command,
                "args": args,
                "environment": environment,
            }
        return payload

    @field_validator("enabled_tools")
    @classmethod
    def validate_enabled_tools(cls, value: list[str]) -> list[str]:
        if any(not name.strip() or "\x00" in name for name in value):
            raise ValueError("enabledTools names must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("enabledTools may not contain duplicates")
        return value

    @field_validator("methods")
    @classmethod
    def validate_methods(cls, value: dict[str, MCPMethodPolicyConfig]) -> dict[str, MCPMethodPolicyConfig]:
        if any(not name or "\x00" in name for name in value):
            raise ValueError("method policy names must be non-empty")
        return value

    @model_validator(mode="after")
    def validate_transport(self) -> "MCPServerConfig":
        if self.transport == "stdio" and (self.stdio is None or self.http is not None):
            raise ValueError("stdio transport requires stdio configuration and forbids http configuration")
        if self.transport == "streamable_http" and (self.http is None or self.stdio is not None):
            raise ValueError("streamable_http transport requires http configuration and forbids stdio configuration")
        return self

    # Compatibility accessors keep existing runtime code and legacy tests working
    # while persisted configuration is normalized to the discriminated shape.
    @property
    def command(self) -> str:
        if self.stdio is None:
            raise AttributeError("HTTP MCP servers do not have a command")
        return "docker" if self.stdio.source == "docker_image" else str(self.stdio.command)

    @property
    def args(self) -> list[str]:
        if self.stdio is None:
            return []
        return ["run", "--rm", "-i", str(self.stdio.image)] if self.stdio.source == "docker_image" else self.stdio.args

    @property
    def environment(self) -> dict[str, str]:
        return self.stdio.environment if self.stdio is not None else {}

    @property
    def workspace_mount(self) -> WorkspaceMountConfig:
        return self.stdio.workspace_mount if self.stdio is not None else WorkspaceMountConfig()

    @property
    def docker(self) -> DockerSecurityConfig | None:
        return self.stdio.docker if self.stdio is not None else None


class MCPConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    version: Literal[1] = 1
    max_concurrency: int = Field(default=4, alias="maxConcurrency", ge=1, le=128)
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    _source_hash: str | None = PrivateAttr(default=None)

    @field_validator("servers")
    @classmethod
    def validate_servers(cls, value: dict[str, MCPServerConfig]) -> dict[str, MCPServerConfig]:
        for server_id in value:
            if not SERVER_ID_RE.fullmatch(server_id):
                raise ValueError(f"invalid MCP server name: {server_id}")
        return value

    def config_hash(self) -> str:
        if self._source_hash:
            return self._source_hash
        encoded = json.dumps(
            self.model_dump(mode="json", by_alias=True), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _legacy_docker_image_and_tmpfs(args: list[str]) -> tuple[str, dict[str, str]]:
    try:
        run_index = args.index("run")
    except ValueError as exc:
        raise ValueError("legacy docker MCP command must contain the run subcommand") from exc
    image = args[-1].strip() if args else ""
    if not image or image.startswith("-") or len(args) <= run_index + 1:
        raise ValueError("legacy docker MCP command must end with an image name")
    tmpfs: dict[str, str] = {}
    index = run_index + 1
    while index < len(args) - 1:
        if args[index] == "--tmpfs" and index + 1 < len(args) - 1:
            mount, _, options = args[index + 1].partition(":")
            if mount and options:
                tmpfs[mount] = options
            index += 2
            continue
        index += 1
    return image, tmpfs


def load_mcp_config(path: str | Path | None = None) -> tuple[MCPConfig, Path]:
    resolved = Path(path or os.environ.get("TGA_MCP_CONFIG_PATH") or DEFAULT_CONFIG_PATH).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"MCP config does not exist: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid MCP config JSON at line {exc.lineno}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("MCP config root must be an object")
    config = MCPConfig.model_validate(payload)
    config._source_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return config, resolved


def configured_mcp_path() -> Path:
    return Path(os.environ.get("TGA_MCP_CONFIG_PATH") or DEFAULT_CONFIG_PATH).expanduser().resolve()


def mutate_mcp_config(
    path: str | Path,
    update: Callable[[MCPConfig], MCPConfig],
) -> tuple[MCPConfig, Path]:
    """Apply one process-safe read/validate/atomic-write transaction."""
    resolved = Path(path).expanduser().resolve()
    with _CONFIG_WRITE_LOCK:
        config, resolved = load_mcp_config(resolved)
        updated = update(config)
        if not isinstance(updated, MCPConfig):
            raise TypeError("MCP config update must return MCPConfig")
        _atomic_write_config(resolved, updated)
        return updated, resolved


def delete_mcp_server(path: str | Path, server_id: str) -> bool:
    if not SERVER_ID_RE.fullmatch(server_id):
        raise ValueError(f"invalid MCP server name: {server_id}")
    removed = False

    def update(config: MCPConfig) -> MCPConfig:
        nonlocal removed
        if server_id not in config.servers:
            return config
        payload = config.model_dump(mode="json", by_alias=True)
        del payload["servers"][server_id]
        removed = True
        return MCPConfig.model_validate(payload)

    mutate_mcp_config(path, update)
    return removed


def set_mcp_server_enabled(path: str | Path, server_id: str, enabled: bool) -> bool:
    if not SERVER_ID_RE.fullmatch(server_id):
        raise ValueError(f"invalid MCP server name: {server_id}")
    found = False

    def update(config: MCPConfig) -> MCPConfig:
        nonlocal found
        server = config.servers.get(server_id)
        if server is None:
            return config
        found = True
        payload = config.model_dump(mode="json", by_alias=True)
        payload["servers"][server_id]["enabled"] = enabled
        return MCPConfig.model_validate(payload)

    mutate_mcp_config(path, update)
    if not found:
        raise KeyError(server_id)
    return enabled


def upsert_mcp_server(path: str | Path, server_id: str, server: MCPServerConfig) -> str:
    if not SERVER_ID_RE.fullmatch(server_id):
        raise ValueError(f"invalid MCP server name: {server_id}")
    action = "created"

    def update(config: MCPConfig) -> MCPConfig:
        nonlocal action
        if server_id in config.servers:
            action = "updated"
        payload = config.model_dump(mode="json", by_alias=True)
        payload["servers"][server_id] = server.model_dump(mode="json", by_alias=True)
        return MCPConfig.model_validate(payload)

    mutate_mcp_config(path, update)
    return action


def patch_mcp_server(path: str | Path, server_id: str, patch: dict[str, Any]) -> MCPServerConfig:
    if not SERVER_ID_RE.fullmatch(server_id):
        raise ValueError(f"invalid MCP server name: {server_id}")
    if not isinstance(patch, dict):
        raise ValueError("MCP server patch must be an object")
    updated: MCPServerConfig | None = None

    def update(config: MCPConfig) -> MCPConfig:
        nonlocal updated
        current = config.servers.get(server_id)
        if current is None:
            raise KeyError(server_id)
        payload = current.model_dump(mode="json", by_alias=True)
        _merge_patch(payload, patch)
        updated = MCPServerConfig.model_validate(payload)
        root = config.model_dump(mode="json", by_alias=True)
        root["servers"][server_id] = updated.model_dump(mode="json", by_alias=True)
        return MCPConfig.model_validate(root)

    mutate_mcp_config(path, update)
    assert updated is not None
    return updated


def _merge_patch(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_patch(target[key], value)
        else:
            target[key] = value


def _atomic_write_config(path: Path, config: MCPConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump_json(by_alias=True, indent=2, exclude_none=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
