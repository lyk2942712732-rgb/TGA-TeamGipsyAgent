from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tga.tools.mcp_catalog import MCPServerSpec, discover_mcp_security_hub


DEFAULT_REPO = "https://github.com/FuzzingLabs/mcp-security-hub.git"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Clone, inspect, and optionally build mcp-security-hub for TGA.")
    parser.add_argument("--hub-root", required=True, help="Local checkout path for mcp-security-hub.")
    parser.add_argument("--repo-url", default=DEFAULT_REPO)
    parser.add_argument("--no-fetch", action="store_true", help="Use the existing checkout without git fetch/reset.")
    parser.add_argument("--build", action="store_true", help="Build missing MCP images.")
    parser.add_argument("--retries", type=int, default=2, help="Build attempts per image.")
    parser.add_argument("--timeout-seconds", type=int, default=900, help="Timeout per build attempt.")
    parser.add_argument("--no-skip-existing", action="store_true", help="Rebuild images even if they already exist.")
    parser.add_argument("--report-path", help="Optional path to write the JSON bootstrap report.")
    parser.add_argument(
        "--network-profile",
        choices=["default", "cn"],
        default="default",
        help="Build-time mirror/profile hints. 'cn' injects common China-accessible package mirrors where possible.",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Optional server IDs or short names to build, for example nmap nuclei gitleaks.",
    )
    args = parser.parse_args()

    hub_root = Path(args.hub_root).expanduser().resolve()
    if args.no_fetch and not (hub_root / ".git").exists():
        raise SystemExit(f"--no-fetch requested but checkout does not exist: {hub_root}")
    if not args.no_fetch:
        ensure_checkout(hub_root, args.repo_url)
    catalog = discover_mcp_security_hub(hub_root)
    report = {
        "hub_root": str(hub_root),
        "revision": catalog.revision,
        "servers": len(catalog.servers),
        "started_at": utc_now(),
        "build": [],
    }
    if args.build:
        if not docker_daemon_available():
            print(json.dumps({**report, "error": "docker daemon unavailable"}, ensure_ascii=False, indent=2))
            return 1
        selected = select_servers(catalog.servers, args.only)
        build_root = prepare_build_root(hub_root, args.network_profile)
        build_catalog = discover_mcp_security_hub(build_root)
        selected_by_id = {server.id for server in selected}
        selected = [server for server in build_catalog.servers if server.id in selected_by_id]
        report["build_root"] = str(build_root)
        report["network_profile"] = args.network_profile
        for server in selected:
            log(f"building {server.id} ({server.image})")
            result = build_server(
                build_root,
                server,
                retries=args.retries,
                timeout_seconds=args.timeout_seconds,
                skip_existing=not args.no_skip_existing,
            )
            report["build"].append(result)
            log(f"{server.id}: {summarize_build_result(result)}")
            write_report(report, args.report_path)
    report["finished_at"] = utc_now()
    output = json.dumps(report, ensure_ascii=False, indent=2)
    write_report(report, args.report_path)
    print(output)
    failed = [item for item in report["build"] if item.get("status") != "built"]
    return 1 if failed else 0


def ensure_checkout(hub_root: Path, repo_url: str) -> None:
    if (hub_root / ".git").exists():
        subprocess.run(["git", "-C", str(hub_root), "fetch", "--depth=1", "origin", "HEAD"], check=False)
        subprocess.run(["git", "-C", str(hub_root), "reset", "--hard", "FETCH_HEAD"], check=False)
        return
    hub_root.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth=1", repo_url, str(hub_root)], check=True)


def prepare_build_root(hub_root: Path, network_profile: str) -> Path:
    if network_profile == "default":
        return hub_root

    build_root = hub_root.parent / f"{hub_root.name}-build-{network_profile}"
    if build_root.exists():
        shutil.rmtree(build_root)
    ignore = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache")
    shutil.copytree(hub_root, build_root, ignore=ignore)
    if network_profile == "cn":
        patch_cn_network_profile(build_root)
    return build_root


def patch_cn_network_profile(build_root: Path) -> None:
    for dockerfile in build_root.rglob("Dockerfile"):
        text = dockerfile.read_text(encoding="utf-8")
        original = text
        text = _patch_alpine_apk(text)
        text = _patch_python_pip(text)
        text = _patch_node_npm(text)
        text = _patch_go_download(text)
        text = _patch_go_proxy(text)
        text = _patch_solazy_allocative_pin(text)
        if text != original:
            dockerfile.write_text(text, encoding="utf-8")


def _patch_debian_apt(text: str) -> str:
    marker = "# TGA_CN_APT_MIRROR"
    if marker in text or "apt-get update" not in text:
        return text
    setup = (
        f"{marker}\n"
        "RUN "
        "if [ -f /etc/apt/sources.list.d/debian.sources ]; then "
        "sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g; "
        "s|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' "
        "/etc/apt/sources.list.d/debian.sources; "
        "elif [ -f /etc/apt/sources.list ]; then "
        "sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g; "
        "s|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' "
        "/etc/apt/sources.list; fi\n\n"
    )
    return _insert_after_last_from(text, setup)


def _patch_alpine_apk(text: str) -> str:
    marker = "# TGA_CN_APK_MIRROR"
    if marker in text or "apk add" not in text:
        return text
    setup = (
        f"{marker}\n"
        "RUN "
        "sed -i 's|https://dl-cdn.alpinelinux.org/alpine|https://mirrors.tuna.tsinghua.edu.cn/alpine|g; "
        "s|http://dl-cdn.alpinelinux.org/alpine|https://mirrors.tuna.tsinghua.edu.cn/alpine|g' "
        "/etc/apk/repositories\n\n"
    )
    return _insert_after_last_from(text, setup)


def _patch_python_pip(text: str) -> str:
    marker = "# TGA_CN_PIP_MIRROR"
    if marker in text or "pip install" not in text:
        return text
    setup = (
        f"{marker}\n"
        "ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \\\n"
        "    PIP_EXTRA_INDEX_URL=https://pypi.org/simple \\\n"
        "    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \\\n"
        "    PIP_DEFAULT_TIMEOUT=120 \\\n"
        "    PIP_RETRIES=10\n\n"
    )
    return _insert_after_last_from(text, setup)


def _patch_node_npm(text: str) -> str:
    marker = "# TGA_CN_NPM_MIRROR"
    if marker in text or not any(token in text for token in ("npm install", "npm ci", "yarn")):
        return text
    setup = (
        f"{marker}\n"
        "RUN "
        "npm config set registry https://registry.npmmirror.com && "
        "npm config set fetch-retries 5 && "
        "npm config set fetch-timeout 120000\n\n"
    )
    return _insert_after_last_from(text, setup)


def _patch_go_download(text: str) -> str:
    marker = "# TGA_CN_GO_APT_FALLBACK"
    if marker in text or "go.dev/dl/go" not in text:
        return text
    replacement = (
        f"{marker}\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends golang-go && "
        "rm -rf /var/lib/apt/lists/* && "
        "ln -sfn /usr/lib/go /usr/local/go && "
        "go version\n"
    )
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("RUN ") and "go.dev/dl/go" in _collect_run_block(lines, index)[0]:
            _, next_index = _collect_run_block(lines, index)
            output.append(replacement.rstrip("\n"))
            index = next_index
            continue
        output.append(line)
        index += 1
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def _patch_go_proxy(text: str) -> str:
    marker = "# TGA_CN_GO_PROXY"
    if marker in text or "go install " not in text:
        return text
    setup = (
        f"{marker}\n"
        "ENV GOPROXY=https://goproxy.cn,direct \\\n"
        "    GOSUMDB=sum.golang.google.cn\n\n"
    )
    return _insert_after_last_from(text, setup)


def _patch_solazy_allocative_pin(text: str) -> str:
    marker = "# TGA_SOLAZY_ALLOCATIVE_PIN"
    if marker in text or "FuzzingLabs/sol-azy.git" not in text:
        return text
    return text.replace(
        "RUN cargo build --release",
        f"{marker}\nRUN cargo update -p allocative --precise 0.3.4 && cargo build --release",
    )


def _collect_run_block(lines: list[str], start: int) -> tuple[str, int]:
    block = [lines[start]]
    index = start + 1
    while block[-1].rstrip().endswith("\\") and index < len(lines):
        block.append(lines[index])
        index += 1
    return "\n".join(block), index


def _insert_after_last_from(text: str, snippet: str) -> str:
    lines = text.splitlines(keepends=True)
    insert_at = 0
    for index, line in enumerate(lines):
        if line.lstrip().upper().startswith("FROM "):
            insert_at = index + 1
    lines.insert(insert_at, snippet)
    return "".join(lines)


def select_servers(servers: list[MCPServerSpec], only: list[str] | None) -> list[MCPServerSpec]:
    if not only:
        return servers
    wanted = {item.lower().replace("_", "-").removesuffix("-mcp") for item in only}
    return [
        server
        for server in servers
        if server.id.lower().removesuffix("-mcp") in wanted or server.short_name.lower() in wanted
    ]


def build_server(
    hub_root: Path,
    server: MCPServerSpec,
    *,
    retries: int,
    timeout_seconds: int,
    skip_existing: bool,
) -> dict[str, object]:
    command = build_command(hub_root, server)
    if skip_existing and image_exists(server.image):
        return {
            "tool": server.id,
            "status": "built",
            "detail": f"image already present: {server.image}",
            "command": command,
            "attempts": [],
        }
    attempts = []
    for attempt in range(1, retries + 1):
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
                encoding="utf-8",
                errors="replace",
            )
            stdout_tail = (completed.stdout or "")[-4000:]
            stderr_tail = (completed.stderr or "")[-4000:]
            returncode = completed.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout_tail = (exc.stdout or "")[-4000:]
            stderr_tail = (exc.stderr or "")[-4000:]
            returncode = 124
            timed_out = True
        failure_class = None if returncode == 0 else classify_failure(stdout_tail + "\n" + stderr_tail, timed_out=timed_out)
        attempts.append(
            {
                "attempt": attempt,
                "returncode": returncode,
                "timed_out": timed_out,
                "failure_class": failure_class,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            }
        )
        if returncode == 0:
            return {"tool": server.id, "status": "built", "command": command, "attempts": attempts}
    return {"tool": server.id, "status": "failed", "command": command, "attempts": attempts}


def summarize_build_result(result: dict[str, object]) -> str:
    status = str(result.get("status", "unknown"))
    detail = result.get("detail")
    if isinstance(detail, str) and detail:
        return f"{status} ({detail})"
    attempts = result.get("attempts")
    if isinstance(attempts, list) and attempts:
        last = attempts[-1]
        if isinstance(last, dict):
            failure = last.get("failure_class")
            returncode = last.get("returncode")
            if failure:
                return f"{status} ({failure}, returncode={returncode})"
            return f"{status} (returncode={returncode})"
    return status


def write_report(report: dict[str, object], report_path: str | None) -> None:
    if not report_path:
        return
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def log(message: str) -> None:
    print(f"[tga-mcp-bootstrap] {message}", file=sys.stderr, flush=True)


def build_command(hub_root: Path, server: MCPServerSpec) -> list[str]:
    compose_file = hub_root / "docker-compose.yml"
    if server.compose_service and compose_file.exists():
        command = ["docker", "compose", "-f", str(compose_file)]
        for profile in server.profiles:
            command.extend(["--profile", profile])
        command.extend(["build", server.compose_service])
        return command
    return ["docker", "build", "-t", server.image, str(hub_root / server.path)]


def docker_daemon_available() -> bool:
    return (
        subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            text=True,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def image_exists(image: str) -> bool:
    return subprocess.run(
        ["docker", "image", "inspect", image],
        text=True,
        capture_output=True,
        check=False,
    ).returncode == 0


def classify_failure(text: str, *, timed_out: bool = False) -> str:
    lowered = text.lower()
    if timed_out:
        return "timeout"
    if (
        "tls handshake timeout" in lowered
        or "tls connect error" in lowered
        or "unexpected eof while reading" in lowered
        or "tls:" in lowered
    ):
        return "network_tls"
    if "readtimeout" in lowered or "read timed out" in lowered or "max retries exceeded" in lowered:
        return "network_timeout"
    if "proxy.golang.org" in lowered or "goproxy" in lowered:
        return "go_proxy"
    if "429 too many requests" in lowered or "too many requests" in lowered:
        return "registry_rate_limit"
    if "curl:" in lowered and "exit code" in lowered:
        return "network_download"
    if "failed to resolve source metadata" in lowered or "failed to fetch anonymous token" in lowered:
        return "registry_auth_or_metadata"
    if "unable to select packages" in lowered or "apk add" in lowered:
        return "apk_package_resolution"
    if "apt-get" in lowered and "exit code" in lowered:
        return "apt_package_resolution"
    if "pip install" in lowered or "no matching distribution found" in lowered:
        return "python_dependency_resolution"
    if "cargo build" in lowered or "could not compile" in lowered or "rustc --explain" in lowered:
        return "rust_dependency_resolution"
    if "npm" in lowered or "node" in lowered:
        return "node_dependency_resolution"
    return "unknown"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
