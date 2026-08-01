"""Typed mappings from capability arguments to Kali process argv."""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import shlex
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Callable
from urllib.parse import urlparse

from tga.sandbox.models import ProcessSpec


Adapter = Callable[[dict[str, Any], int], ProcessSpec]

SHELL_AUXILIARY_COMMANDS = {
    "base64", "cat", "cmp", "cut", "diff", "echo", "file", "grep",
    "head", "hexdump", "jq", "ls", "md5sum", "od", "printf", "pwd",
    "rg", "sha256sum", "sort", "stat", "strings", "tail", "tr", "uniq",
    "wc", "xxd",
}


def process_spec(capability: str, arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    try:
        adapter = ADAPTERS[capability]
    except KeyError as exc:
        raise ValueError(f"sandbox capability has no typed adapter: {capability}") from exc
    return adapter(arguments, timeout)


def _sandbox_exec(arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    executable = str(arguments["executable"])
    argv = tuple(str(item) for item in arguments.get("argv") or ())
    for token in argv:
        normalized = token.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or PureWindowsPath(token).is_absolute() or ".." in path.parts:
            raise ValueError(
                "sandbox.exec argv paths must remain relative to the SolverRun workspace"
            )
    return ProcessSpec(
        argv=(executable, *argv),
        logical_workspace="solver",
        timeout_seconds=min(int(arguments.get("timeout") or timeout), timeout),
    )


def _workspace_shell(arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    command = str(arguments["command"])
    if any(value in command for value in ("\n", "\r", "\x00")):
        raise ValueError("workspace.shell accepts one auxiliary command")
    try:
        argv = tuple(shlex.split(command, posix=True))
    except ValueError as exc:
        raise ValueError("invalid workspace.shell command") from exc
    if not argv or len(argv) > 128:
        raise ValueError("workspace.shell command is empty or too large")
    executable = PurePosixPath(argv[0].replace("\\", "/")).name
    if executable not in SHELL_AUXILIARY_COMMANDS:
        raise ValueError(
            "workspace.shell only permits catalogued local auxiliary commands"
        )
    if any(token in {"|", "||", "&&", ";", ">", ">>", "<", "<<"} for token in argv):
        raise ValueError("workspace.shell control operators are forbidden")
    for token in argv[1:]:
        candidate = token.split("=", 1)[-1].replace("\\", "/")
        path = PurePosixPath(candidate)
        if (
            path.is_absolute()
            or PureWindowsPath(candidate).is_absolute()
            or ".." in path.parts
        ):
            raise ValueError("workspace.shell paths must remain in the Solver workspace")
    wrapper = r'''
import os, pathlib, sys

private_root = pathlib.Path.cwd().resolve()
workspace_root = private_root.parents[1]
command = sys.argv[1]
arguments = sys.argv[2:]
for token in arguments:
    if token.startswith("-") and "=" not in token:
        if "/" in token or ".." in token:
            raise PermissionError("workspace.shell option path escapes the Solver workspace")
        continue
    candidate = token.split("=", 1)[-1]
    path = pathlib.Path(candidate)
    resolved = (private_root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError:
        continue
    try:
        resolved.relative_to(private_root)
    except ValueError as exc:
        raise PermissionError("workspace.shell path escapes the Solver workspace") from exc
os.execvp(command, [command, *arguments])
'''
    encoded_wrapper = base64.b64encode(wrapper.encode("utf-8")).decode("ascii")
    return ProcessSpec(
        argv=(
            "python3", "-I", "-c",
            "import base64,sys;script=base64.b64decode(sys.argv[1]);sys.argv=[sys.argv[0],*sys.argv[2:]];exec(script)",
            encoded_wrapper,
            *argv,
        ),
        logical_workspace="solver",
        timeout_seconds=min(int(arguments.get("timeout") or timeout), timeout),
    )


def _workspace_python(arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    argv = tuple(str(item) for item in arguments.get("argv") or ())
    audit = r'''
import os, pathlib, sys

private_root = pathlib.Path.cwd().resolve()
workspace_root = private_root.parents[1]
readonly_roots = (
    workspace_root / "inputs",
    workspace_root / "shared",
)
blocked_events = (
    "subprocess.", "socket.", "ctypes.dlopen", "os.system", "os.posix_spawn",
    "os.spawn", "os.exec", "os.fork",
)
mutation_events = (
    "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.symlink", "os.link",
    "os.chmod", "os.chown", "os.truncate",
)

def inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

def workspace_path(value):
    if not isinstance(value, (str, bytes, os.PathLike)):
        return None
    path = pathlib.Path(os.fsdecode(value))
    return (private_root / path).resolve() if not path.is_absolute() else path.resolve()

def audit(event, args):
    if event.startswith(blocked_events):
        raise PermissionError("workspace.python audit policy blocked " + event)
    if event == "open" and args:
        path = workspace_path(args[0])
        if path is None or not inside(path, workspace_root):
            return
        mode = str(args[1]) if len(args) > 1 else "r"
        flags = int(args[2]) if len(args) > 2 and isinstance(args[2], int) else 0
        writing = any(value in mode for value in "wax+") or bool(
            flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        )
        if inside(path, private_root) and not any(inside(path, root) for root in readonly_roots):
            return
        if not writing and any(inside(path, root) for root in readonly_roots):
            return
        raise PermissionError("workspace.python path is outside the Solver workspace")
    if event.startswith(mutation_events) and args:
        paths = [workspace_path(value) for value in args[:2]]
        if any(path is not None and inside(path, workspace_root) and not inside(path, private_root) for path in paths):
            raise PermissionError("workspace.python mutation is outside the Solver workspace")

sys.addaudithook(audit)
'''
    encoded_audit = base64.b64encode(audit.encode("utf-8")).decode("ascii")
    audit_bootstrap = "exec(base64.b64decode(sys.argv[1]));del sys.argv[1];"
    if arguments.get("source") is not None:
        encoded = base64.b64encode(str(arguments["source"]).encode("utf-8")).decode("ascii")
        bootstrap = (
            "import base64,sys;"
            + audit_bootstrap +
            "source=base64.b64decode(sys.argv[1]);"
            "code=compile(source,'<tga-workspace-python>','exec');"
            "sys.argv=['<tga-workspace-python>',*sys.argv[2:]];exec(code)"
        )
        command = ("python3", "-I", "-c", bootstrap, encoded_audit, encoded, *argv)
    else:
        path = _relative_path(str(arguments.get("script_path") or ""))
        bootstrap = (
            "import base64,pathlib,sys;"
            + audit_bootstrap +
            "path=sys.argv[1];source=pathlib.Path(path).read_bytes();"
            "code=compile(source,path,'exec');sys.argv=[path,*sys.argv[2:]];exec(code)"
        )
        command = ("python3", "-I", "-c", bootstrap, encoded_audit, path, *argv)
    return ProcessSpec(
        argv=command,
        logical_workspace="solver",
        timeout_seconds=min(int(arguments.get("timeout") or timeout), timeout),
    )


def _workspace_write(arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    path = _writable_path(str(arguments["relative_path"]))
    encoded = base64.b64encode(str(arguments["content"]).encode("utf-8")).decode("ascii")
    script = (
        "import base64,pathlib,sys;"
        "p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);"
        "p.write_bytes(base64.b64decode(sys.argv[2]));print(p.as_posix())"
    )
    return ProcessSpec(
        argv=("python3", "-I", "-c", script, path, encoded),
        logical_workspace="solver",
        timeout_seconds=timeout,
    )


def _http_request(arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    url = str(arguments.get("url") or "")
    if not url:
        raise ValueError("sandbox HTTP execution requires an absolute governed URL")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid governed HTTP URL")
    blocked_headers = {
        "connection", "content-length", "host", "proxy-authorization",
        "transfer-encoding",
    }
    headers = {}
    for name, value in dict(arguments.get("headers") or {}).items():
        if str(name).casefold() in blocked_headers:
            continue
        if "\r" in str(name) or "\n" in str(name) or "\r" in str(value) or "\n" in str(value):
            raise ValueError("HTTP headers may not contain newlines")
        headers[str(name)] = str(value)
    payload = json.dumps(
        {**arguments, "headers": headers},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    script = r'''
import base64, http.client, json, socket, ssl, sys, urllib.parse

a = json.loads(base64.b64decode(sys.argv[1]))
u = a["url"]
q = urllib.parse.urlencode(a.get("query") or {})
u = u + ("&" if "?" in u else "?") + q if q else u
p = urllib.parse.urlsplit(u)
b = a.get("body")
f = a.get("body_format")
if f == "json":
    d = json.dumps(b).encode() if b is not None else None
elif f == "form" and isinstance(b, dict):
    d = urllib.parse.urlencode(b).encode()
else:
    d = str(b).encode() if b is not None else None
h = dict(a.get("headers") or {})
if d is not None and f == "json":
    h.setdefault("Content-Type", "application/json")

class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, pinned_ip, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(host, **kwargs)
    def connect(self):
        self.sock = socket.create_connection((self.pinned_ip, self.port), self.timeout)

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, pinned_ip, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(host, **kwargs)
    def connect(self):
        sock = socket.create_connection((self.pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)

path = urllib.parse.urlunsplit(("", "", p.path or "/", p.query, ""))
last_error = None
for address in a.get("_approved_addresses") or []:
    try:
        cls = PinnedHTTPS if p.scheme == "https" else PinnedHTTP
        kwargs = {"timeout": a.get("timeout", 12)}
        if p.scheme == "https":
            kwargs["context"] = ssl.create_default_context()
        connection = cls(p.hostname, address, port=p.port, **kwargs)
        connection.request(a.get("method", "GET"), path, body=d, headers=h)
        response = connection.getresponse()
        body = response.read()
        print(json.dumps({
            "status": response.status,
            "final_url": u,
            "headers": dict(response.getheaders()),
            "body_base64": base64.b64encode(body).decode(),
            "redirect_requires_authorization": response.status in {301, 302, 303, 307, 308},
        }))
        break
    except Exception as exc:
        last_error = str(exc)
else:
    raise RuntimeError(last_error or "no authorized address")
'''
    bootstrap = (
        "import base64,sys;script=base64.b64decode(sys.argv[1]);"
        "sys.argv=[sys.argv[0],*sys.argv[2:]];exec(script)"
    )
    encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return ProcessSpec(
        argv=("python3", "-I", "-c", bootstrap, encoded_script, encoded),
        logical_workspace="solver",
        timeout_seconds=min(int(arguments.get("timeout") or timeout), timeout),
    )


def _nmap(arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    target = _network_target(str(arguments["target"]))
    ports = str(arguments.get("ports") or "1-1000")
    if not re.fullmatch(r"[0-9,-]{1,128}", ports):
        raise ValueError("invalid nmap ports")
    argv = ["nmap", "-n", "-Pn", "-sT", "-p", ports]
    if arguments.get("service_detection"):
        argv.append("-sV")
    argv.extend(("--", target))
    return ProcessSpec(argv=tuple(argv), timeout_seconds=timeout)


def _ffuf(arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    url = str(arguments["url"])
    if "FUZZ" not in url or urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("ffuf url must be an absolute URL containing FUZZ")
    wordlist = _relative_path(str(arguments["wordlist"]))
    argv = ["ffuf", "-noninteractive", "-of", "json", "-u", url, "-w", wordlist]
    if arguments.get("match_codes"):
        argv.extend(("-mc", ",".join(str(int(item)) for item in arguments["match_codes"])))
    return ProcessSpec(argv=tuple(argv), timeout_seconds=timeout)


def _nuclei(arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    target = str(arguments["target"])
    if urlparse(target).scheme not in {"http", "https"}:
        raise ValueError("nuclei target must be an absolute HTTP URL")
    argv = ["nuclei", "-jsonl", "-u", target]
    for tag in arguments.get("tags") or ():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(tag)):
            raise ValueError("invalid nuclei tag")
    if arguments.get("tags"):
        argv.extend(("-tags", ",".join(str(item) for item in arguments["tags"])))
    return ProcessSpec(argv=tuple(argv), timeout_seconds=timeout)


def _binwalk(arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    path = _relative_path(str(arguments["path"]))
    argv = ["binwalk"]
    if arguments.get("extract"):
        argv.append("--extract")
    argv.extend(("--", path))
    return ProcessSpec(argv=tuple(argv), timeout_seconds=timeout)


def _yara(arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    rules = _relative_path(str(arguments["rules_path"]))
    target = _relative_path(str(arguments["target_path"]))
    argv = ["yara"]
    if arguments.get("recursive"):
        argv.append("-r")
    argv.extend((rules, target))
    return ProcessSpec(argv=tuple(argv), timeout_seconds=timeout)


def _radare2(arguments: dict[str, Any], timeout: int) -> ProcessSpec:
    path = _relative_path(str(arguments["path"]))
    commands = arguments.get("commands") or ["aaa", "afl"]
    argv = ["radare2", "-2", "-q", "-e", "scr.color=false"]
    for command in commands:
        value = str(command)
        if len(value) > 512 or "\x00" in value or "!" in value:
            raise ValueError("unsafe radare2 command")
        argv.extend(("-c", value))
    argv.extend(("--", path))
    return ProcessSpec(argv=tuple(argv), timeout_seconds=timeout)


def _relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized or path.is_absolute() or PureWindowsPath(normalized).is_absolute()
        or ".." in path.parts
    ):
        raise ValueError("path must remain relative to the Solver workspace")
    return normalized


def _writable_path(value: str) -> str:
    normalized = _relative_path(value)
    if PurePosixPath(normalized).parts[0] not in {"scratch", "outputs"}:
        raise ValueError("workspace writes must remain under scratch/ or outputs/")
    return normalized


def _network_target(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise ValueError("network scanner targets must be canonical IP addresses") from exc


ADAPTERS: dict[str, Adapter] = {
    "sandbox.exec": _sandbox_exec,
    "workspace.shell": _workspace_shell,
    "workspace.python": _workspace_python,
    "workspace.write": _workspace_write,
    "http.request": _http_request,
    "nmap.scan": _nmap,
    "ffuf.directory_scan": _ffuf,
    "nuclei.scan": _nuclei,
    "binwalk.analyze": _binwalk,
    "yara.scan": _yara,
    "radare2.analyze": _radare2,
}


__all__ = ["ADAPTERS", "process_spec"]
