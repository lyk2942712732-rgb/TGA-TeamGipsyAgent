from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from tga.capabilities.guards import (
    local_path_in_roots,
    local_scope_roots,
    redirect_in_scope,
    redact_headers,
    safe_join,
    target_in_scope,
    truncate_text,
)
from tga.capabilities.models import (
    ActionError,
    ActionResult,
    ActionSpec,
    ArtifactInspectInput,
    HTTPRequestInput,
    ToolInvokeInput,
    WorkspaceBinaryInput,
    WorkspacePythonInput,
)
from tga.capabilities.registry import default_hub_root
from tga.contracts import ArtifactRecord, Intent, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.tools.mcp_catalog import discover_mcp_security_hub
from tga.tools.tool_runner import ToolRunner


BLOCKED_PYTHON_TOKENS = (
    "import socket",
    "from socket",
    "import requests",
    "from requests",
    "import urllib",
    "from urllib",
    "http.client",
    "subprocess",
    "os.environ",
    "winreg",
)


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class CapabilityExecutor:
    def __init__(
        self,
        *,
        run_root: str | Path = "runs",
        hub_root: str | Path | None = None,
        mcp_client: Any | None = None,
    ):
        self.run_root = Path(run_root).resolve()
        self.hub_root = Path(hub_root).resolve() if hub_root else default_hub_root()
        self.mcp_client = mcp_client

    def execute(self, spec: ActionSpec) -> ActionResult:
        workspace = SolverWorkspace(self.run_root, spec.task_id, spec.solver_id)
        try:
            if spec.capability == "http.request":
                return self._http_request(spec, workspace)
            if spec.capability == "tool.invoke":
                return self._tool_invoke(spec, workspace)
            if spec.capability == "workspace.python":
                return self._workspace_python(spec, workspace)
            if spec.capability == "workspace.binary":
                return self._workspace_binary(spec, workspace)
            if spec.capability == "artifact.inspect":
                return self._artifact_inspect(spec, workspace)
        except Exception as exc:
            return self._finish(
                spec,
                workspace,
                status="failed",
                summary=f"{spec.capability} failed",
                payload={"error": {"code": "CAPABILITY_ERROR", "message": str(exc)}},
                error=ActionError(code="CAPABILITY_ERROR", message=str(exc)),
            )
        return self._finish(
            spec,
            workspace,
            status="blocked",
            summary="capability is not registered",
            payload={"error": {"code": "CAPABILITY_NOT_REGISTERED", "message": spec.capability}},
            error=ActionError(code="CAPABILITY_NOT_REGISTERED", message=spec.capability),
        )

    def _http_request(self, spec: ActionSpec, workspace: "SolverWorkspace") -> ActionResult:
        params = HTTPRequestInput.model_validate(spec.arguments)
        if params.url.lower().startswith(("ws:", "wss:")):
            return self._blocked(spec, workspace, "WEBSOCKET_NOT_ALLOWED", "websocket requests are not supported")
        if not params.url.lower().startswith(("http://", "https://")):
            return self._blocked(spec, workspace, "INVALID_URL", "only http and https URLs are supported")
        if not target_in_scope(params.url, spec.scope):
            return self._blocked(spec, workspace, "OUT_OF_SCOPE", "request URL is not in scope")
        if spec.intensity == "passive" and params.method not in {"GET", "HEAD"}:
            return self._blocked(spec, workspace, "ACTIVE_SCAN_NOT_ALLOWED", "passive intensity allows only GET/HEAD")

        visited: list[str] = []
        current_url = params.url
        opener = build_opener(NoRedirectHandler)
        body_bytes = params.body.encode("utf-8") if params.body is not None else None
        for redirect_index in range(params.max_redirects + 1):
            visited.append(current_url)
            request = Request(
                current_url,
                data=body_bytes if params.method != "GET" else None,
                method=params.method,
                headers=params.headers,
            )
            try:
                response = opener.open(request, timeout=params.timeout_seconds)
                status = getattr(response, "status", 200)
                headers = dict(response.headers.items())
                data = response.read(params.max_output_bytes + 1) if params.method != "HEAD" else b""
                break
            except HTTPError as exc:
                status = exc.code
                headers = dict(exc.headers.items()) if exc.headers else {}
                if status in {301, 302, 303, 307, 308} and headers.get("Location"):
                    allowed, redirected = redirect_in_scope(current_url, headers["Location"], spec.scope)
                    if not allowed:
                        return self._blocked(
                            spec,
                            workspace,
                            "OUT_OF_SCOPE_REDIRECT",
                            f"redirect target is outside scope: {redirected}",
                            extra={"visited": visited, "redirect": redirected},
                        )
                    if not params.allow_redirects:
                        data = b""
                        break
                    if redirect_index == params.max_redirects:
                        return self._blocked(spec, workspace, "REDIRECT_LIMIT", "redirect limit exceeded")
                    current_url = redirected
                    if status == 303:
                        params.method = "GET"
                        body_bytes = None
                    continue
                data = exc.read(params.max_output_bytes + 1)
                break
            except URLError as exc:
                return self._finish(
                    spec,
                    workspace,
                    status="failed",
                    summary="HTTP request failed",
                    payload={"url": current_url, "error": str(exc.reason)},
                    error=ActionError(code="HTTP_ERROR", message=str(exc.reason), retryable=True),
                    kind="http_response",
                    target=params.url,
                )
        else:
            return self._blocked(spec, workspace, "REDIRECT_LIMIT", "redirect limit exceeded")

        if _looks_like_download(headers):
            return self._blocked(spec, workspace, "DOWNLOAD_NOT_ALLOWED", "download responses are not allowed")

        decoded = data.decode(_charset(headers), errors="replace")
        text, truncated = truncate_text(decoded, params.max_output_bytes)
        payload = {
            "url": params.url,
            "final_url": current_url,
            "method": params.method,
            "status_code": status,
            "headers": redact_headers(headers),
            "visited": visited,
            "body": text,
            "truncated": truncated,
        }
        return self._finish(
            spec,
            workspace,
            status="ok",
            summary=f"HTTP {params.method} {status} {current_url}",
            payload=payload,
            kind="http_response",
            target=params.url,
            output_truncated=truncated,
            raw_for_flags=text,
        )

    def _tool_invoke(self, spec: ActionSpec, workspace: "SolverWorkspace") -> ActionResult:
        params = ToolInvokeInput.model_validate(spec.arguments)
        if not self.hub_root.exists():
            return self._blocked(spec, workspace, "MCP_UNAVAILABLE", f"hub root not found: {self.hub_root}")
        catalog = discover_mcp_security_hub(self.hub_root)
        server = catalog.resolve_server_for_tool(params.tool)
        if server is None:
            return self._blocked(spec, workspace, "TOOL_NOT_AVAILABLE", f"tool is not registered: {params.tool}")
        mcp_tool = params.mcp_tool or (server.tools[0].name if server.tools else params.tool)
        tool_spec = next((item for item in server.tools if item.name == mcp_tool), None)
        if tool_spec is None and server.tools:
            return self._blocked(spec, workspace, "MCP_METHOD_NOT_AVAILABLE", f"unknown MCP method: {mcp_tool}")
        schema_error = _validate_json_schema(params.arguments, tool_spec.input_schema if tool_spec else {})
        if schema_error:
            return self._blocked(spec, workspace, "MCP_SCHEMA_INVALID", schema_error)

        task = TGATask(
            id=spec.task_id,
            name=spec.task_id,
            mode=spec.mode,
            target=params.target or spec.target,
            scope=spec.scope,
            intensity=spec.intensity,
            allow_active_scan=spec.allow_active_scan,
            goal="capability action",
            flag_format=spec.flag_format,
        )
        intent = Intent(
            id=spec.action_id,
            task_id=spec.task_id,
            kind="recon",
            target=params.target or spec.target,
            goal="capability action",
        )
        runner = ToolRunner(catalog=catalog, artifact_store=workspace.artifact_store, mcp_client=self.mcp_client)
        artifact = runner.run_tool(
            task=task,
            intent=intent,
            tool=server.id,
            target=params.target or spec.target,
            args={**params.arguments, "mcp_tool": mcp_tool, "timeout_seconds": params.timeout_seconds},
        )
        text = workspace.read_artifact(artifact)
        text, truncated = truncate_text(text, params.max_output_bytes)
        return ActionResult(
            task_id=spec.task_id,
            solver_id=spec.solver_id,
            action_id=spec.action_id,
            capability=spec.capability,
            status="ok" if '"status": "ok"' in text else "failed",
            summary=f"tool.invoke {server.id}:{mcp_tool}",
            artifacts=[artifact],
            candidate_flags=_candidate_flags(spec.flag_format, text),
            output_truncated=truncated,
        )

    def _workspace_python(self, spec: ActionSpec, workspace: "SolverWorkspace") -> ActionResult:
        params = WorkspacePythonInput.model_validate(spec.arguments)
        blocked = _blocked_python_reason(params.code)
        if blocked:
            return self._blocked(spec, workspace, "WORKSPACE_PYTHON_DENIED", blocked)
        script = safe_join(workspace.workspace_dir, f"{_filesystem_segment(spec.action_id)}.py")
        wrapper = safe_join(workspace.workspace_dir, f"{_filesystem_segment(spec.action_id)}_wrapper.py")
        script.write_text(params.code, encoding="utf-8")
        wrapper.write_text(_python_audit_wrapper(workspace.workspace_dir, local_scope_roots(spec.scope)), encoding="utf-8")
        command = [sys.executable, "-I", str(wrapper), str(script), *params.argv]
        try:
            completed = subprocess.run(
                command,
                input=params.stdin,
                text=True,
                capture_output=True,
                cwd=workspace.workspace_dir,
                env={"PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1"},
                timeout=params.timeout_seconds,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            timed_out = False
            returncode = completed.returncode
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = 124
            stdout = _coerce_process_text(exc.stdout)
            stderr = _coerce_process_text(exc.stderr)

        stdout, stdout_truncated = truncate_text(stdout, params.max_output_bytes)
        stderr, stderr_truncated = truncate_text(stderr, params.max_output_bytes)
        payload = {
            "command": command,
            "returncode": returncode,
            "timed_out": timed_out,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
        runtime_error = None
        if timed_out:
            status = "timeout"
        elif returncode == 0:
            status = "ok"
        elif "workspace.python" in stderr and "denied" in stderr:
            status = "blocked"
            runtime_error = ActionError(code="WORKSPACE_PYTHON_DENIED", message="workspace python runtime policy denied")
        else:
            status = "failed"
        return self._finish(
            spec,
            workspace,
            status=status,
            summary=f"workspace.python returncode={returncode}",
            payload=payload,
            error=runtime_error,
            output_truncated=stdout_truncated or stderr_truncated,
            raw_for_flags=f"{stdout}\n{stderr}",
        )

    def _workspace_binary(self, spec: ActionSpec, workspace: "SolverWorkspace") -> ActionResult:
        params = WorkspaceBinaryInput.model_validate(spec.arguments)
        path = workspace.resolve_read_path(params.path, spec.scope)
        if path is None:
            return self._blocked(spec, workspace, "WORKSPACE_PATH_DENIED", "binary path is outside solver workspace/scope")
        data = path.read_bytes()
        if params.operation == "metadata":
            payload = _binary_metadata(path, data)
            text = json.dumps(payload, ensure_ascii=False)
        elif params.operation == "strings":
            payload = {"path": str(path), "strings": _extract_strings(data, params.min_string)}
            text = "\n".join(payload["strings"])
        else:
            chunk = data[params.offset : params.offset + params.length]
            payload = {"path": str(path), "offset": params.offset, "hexdump": _hexdump(chunk, params.offset)}
            text = payload["hexdump"]
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        rendered, truncated = truncate_text(rendered, params.max_output_bytes)
        return self._finish(
            spec,
            workspace,
            status="ok",
            summary=f"workspace.binary {params.operation} {path.name}",
            payload={"operation": params.operation, "result": rendered, "truncated": truncated},
            output_truncated=truncated,
            raw_for_flags=text,
        )

    def _artifact_inspect(self, spec: ActionSpec, workspace: "SolverWorkspace") -> ActionResult:
        params = ArtifactInspectInput.model_validate(spec.arguments)
        path = workspace.resolve_artifact_path(params.artifact_path)
        if path is None:
            return self._blocked(spec, workspace, "ARTIFACT_NOT_FOUND", "artifact is outside this solver or missing")
        data = path.read_bytes()
        chunk = data[params.offset : params.offset + params.length]
        text = chunk.decode("utf-8", errors="replace")
        hits = _keyword_hits(text, params.keywords, params.context_chars)
        text, truncated = truncate_text(text, params.max_output_bytes)
        payload = {
            "artifact_path": str(path),
            "offset": params.offset,
            "length": len(chunk),
            "text": text,
            "keyword_hits": hits,
            "truncated": truncated,
        }
        return self._finish(
            spec,
            workspace,
            status="ok",
            summary=f"artifact.inspect {path.name} bytes={len(chunk)} hits={len(hits)}",
            payload=payload,
            output_truncated=truncated,
            raw_for_flags=text,
        )

    def _blocked(
        self,
        spec: ActionSpec,
        workspace: "SolverWorkspace",
        code: str,
        message: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> ActionResult:
        payload = {"error": {"code": code, "message": message, "retryable": False}, **(extra or {})}
        return self._finish(
            spec,
            workspace,
            status="blocked",
            summary=message,
            payload=payload,
            error=ActionError(code=code, message=message),
        )

    def _finish(
        self,
        spec: ActionSpec,
        workspace: "SolverWorkspace",
        *,
        status: str,
        summary: str,
        payload: dict[str, Any],
        kind: str = "tool_output",
        target: str | None = None,
        error: ActionError | None = None,
        output_truncated: bool = False,
        raw_for_flags: str = "",
    ) -> ActionResult:
        artifact = workspace.artifact_store.save_text(
            task_id=spec.task_id,
            intent_id=spec.action_id,
            kind=kind,
            text=json.dumps(
                {
                    "task_id": spec.task_id,
                    "solver_id": spec.solver_id,
                    "action_id": spec.action_id,
                    "capability": spec.capability,
                    "status": status,
                    **payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            tool=spec.capability,
            target=target or spec.target,
            suffix=".json",
        )
        return ActionResult(
            task_id=spec.task_id,
            solver_id=spec.solver_id,
            action_id=spec.action_id,
            capability=spec.capability,
            status=status,  # type: ignore[arg-type]
            summary=summary,
            artifacts=[artifact],
            candidate_flags=_candidate_flags(spec.flag_format, raw_for_flags),
            error=error,
            output_truncated=output_truncated,
        )


class SolverWorkspace:
    def __init__(self, run_root: Path, task_id: str, solver_id: str):
        self.root = run_root / _filesystem_segment(task_id) / "solvers" / _filesystem_segment(solver_id)
        self.workspace_dir = self.root / "workspace"
        self.artifact_dir = self.root / "artifacts"
        self.tmp_dir = self.root / "tmp"
        for directory in (self.workspace_dir, self.artifact_dir, self.tmp_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.artifact_store = ArtifactStore(self.artifact_dir)

    def resolve_read_path(self, value: str, scope: list[str]) -> Path | None:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.workspace_dir / value
        try:
            resolved = candidate.expanduser().resolve()
        except (OSError, RuntimeError):
            return None
        roots = [self.workspace_dir, *local_scope_roots(scope)]
        if not resolved.exists() or not local_path_in_roots(resolved, roots):
            return None
        return resolved

    def resolve_artifact_path(self, value: str) -> Path | None:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.artifact_dir / value
        try:
            resolved = candidate.expanduser().resolve()
        except (OSError, RuntimeError):
            return None
        if not resolved.exists() or not local_path_in_roots(resolved, [self.artifact_dir]):
            return None
        return resolved

    def read_artifact(self, artifact: ArtifactRecord) -> str:
        path = self.resolve_artifact_path(artifact.path)
        return path.read_text(encoding="utf-8", errors="replace") if path else ""


def _looks_like_download(headers: dict[str, str]) -> bool:
    disposition = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    content_type = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    return "attachment" in disposition.lower() or content_type.startswith("application/octet-stream")


def _charset(headers: dict[str, str]) -> str:
    content_type = headers.get("Content-Type") or headers.get("content-type") or ""
    match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    return match.group(1) if match else "utf-8"


def _candidate_flags(flag_format: str | None, text: str) -> list[str]:
    if not flag_format or not text:
        return []
    try:
        pattern = re.compile(flag_format)
    except re.error:
        return []
    values: list[str] = []
    for match in pattern.finditer(text):
        value = match.group(0)
        if value not in values:
            values.append(value)
    return values


def _blocked_python_reason(code: str) -> str | None:
    lowered = code.lower()
    for token in BLOCKED_PYTHON_TOKENS:
        if token in lowered:
            return f"blocked token in workspace python code: {token}"
    if re.search(r"[A-Za-z]:[\\/]", code):
        return "absolute host paths are not allowed in workspace python code"
    return None


def _coerce_process_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _filesystem_segment(value: str) -> str:
    stripped = value.strip().strip(".")
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", stripped)
    cleaned = cleaned[:80].strip("._-") or "item"
    if cleaned != value:
        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:8]
        cleaned = f"{cleaned}_{digest}"
    return cleaned


def _python_audit_wrapper(workspace_dir: Path, read_roots: list[Path]) -> str:
    allowed_read_roots = [workspace_dir, *read_roots]
    payload = {
        "workspace": str(workspace_dir.resolve()),
        "allowed_read_roots": [str(path.resolve()) for path in allowed_read_roots],
    }
    return f"""\
import os
import runpy
import sys
from pathlib import Path

POLICY = {json.dumps(payload, ensure_ascii=True)}
WORKSPACE = Path(POLICY["workspace"]).resolve()
READ_ROOTS = [Path(item).resolve() for item in POLICY["allowed_read_roots"]]
PYTHON_ROOTS = {{Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve(), Path(sys.exec_prefix).resolve()}}


def _inside(path, roots):
    try:
        resolved = Path(path).expanduser().resolve()
    except Exception:
        return False
    for root in roots:
        try:
            if os.path.commonpath([str(resolved), str(root)]) == str(root):
                return True
        except Exception:
            continue
    return False


def _is_write_mode(mode, flags):
    text = str(mode or "")
    if any(item in text for item in ("w", "a", "x", "+")):
        return True
    if isinstance(flags, int):
        return bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
    return False


def _audit(event, args):
    if event in {{"socket.__new__", "socket.connect", "socket.bind", "socket.getaddrinfo"}}:
        raise PermissionError("workspace.python network access denied")
    if event in {{"subprocess.Popen", "os.system", "os.startfile", "os.spawn", "os.posix_spawn"}}:
        raise PermissionError("workspace.python process launch denied")
    if event.startswith("winreg.") or event.startswith("ctypes."):
        raise PermissionError("workspace.python host access denied")
    if event == "open" and args:
        target = args[0]
        if isinstance(target, int):
            return
        mode = args[1] if len(args) > 1 else "r"
        flags = args[2] if len(args) > 2 else 0
        roots = [WORKSPACE] if _is_write_mode(mode, flags) else [*READ_ROOTS, *PYTHON_ROOTS]
        if not _inside(target, roots):
            raise PermissionError(f"workspace.python file access denied: {{target}}")


sys.addaudithook(_audit)

if len(sys.argv) < 2:
    raise SystemExit("missing user script")
script = Path(sys.argv[1]).resolve()
if not _inside(script, [WORKSPACE]):
    raise PermissionError("workspace.python script path denied")
sys.argv = [str(script), *sys.argv[2:]]
runpy.run_path(str(script), run_name="__main__")
"""


def _binary_metadata(path: Path, data: bytes) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": str(path),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "magic_hex": data[:16].hex(),
        "type": "elf" if data.startswith(b"\x7fELF") else "pe" if data.startswith(b"MZ") else "data",
    }
    if data.startswith(b"\x7fELF") and len(data) >= 20:
        payload["elf"] = {
            "class": "64-bit" if data[4] == 2 else "32-bit" if data[4] == 1 else "unknown",
            "endianness": "little" if data[5] == 1 else "big" if data[5] == 2 else "unknown",
            "machine": int.from_bytes(data[18:20], "little" if data[5] == 1 else "big"),
        }
    return payload


def _extract_strings(data: bytes, min_length: int) -> list[str]:
    strings: list[str] = []
    current = bytearray()
    for byte in data:
        if 32 <= byte <= 126:
            current.append(byte)
            continue
        if len(current) >= min_length:
            strings.append(current.decode("ascii", errors="replace"))
        current.clear()
    if len(current) >= min_length:
        strings.append(current.decode("ascii", errors="replace"))
    return strings


def _hexdump(data: bytes, start_offset: int = 0) -> str:
    lines: list[str] = []
    for index in range(0, len(data), 16):
        chunk = data[index : index + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in chunk)
        lines.append(f"{start_offset + index:08x}  {hex_part:<47}  {ascii_part}")
    return "\n".join(lines)


def _keyword_hits(text: str, keywords: list[str], context_chars: int) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for keyword in keywords:
        if not keyword:
            continue
        start = 0
        while True:
            index = text.find(keyword, start)
            if index < 0:
                break
            left = max(0, index - context_chars)
            right = min(len(text), index + len(keyword) + context_chars)
            hits.append({"keyword": keyword, "offset": index, "context": text[left:right]})
            start = index + len(keyword)
    return hits


def _validate_json_schema(value: dict[str, Any], schema: dict[str, Any]) -> str | None:
    if not schema:
        return None
    required = schema.get("required", [])
    for key in required:
        if key not in value:
            return f"missing required argument: {key}"
    properties = schema.get("properties", {})
    for key, item in value.items():
        if key not in properties:
            if schema.get("additionalProperties") is False:
                return f"unexpected argument: {key}"
            continue
        expected = properties[key].get("type")
        if expected and not _json_type_matches(item, expected):
            return f"argument {key} should be {expected}"
        enum = properties[key].get("enum")
        if enum and item not in enum:
            return f"argument {key} should be one of {enum}"
    return None


def _json_type_matches(value: Any, expected: str | list[str]) -> bool:
    if isinstance(expected, list):
        return any(_json_type_matches(value, item) for item in expected)
    mapping = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    python_type = mapping.get(expected)
    return isinstance(value, python_type) if python_type else True
