#!/usr/bin/env python3
"""只读验证 TGA3 部署状态，不修改配置、容器或数据。"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Result:
    level: str
    name: str
    detail: str


RESULTS: list[Result] = []


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def record(level: str, name: str, detail: str) -> None:
    RESULTS.append(Result(level, name, detail))
    marker = {"PASS": "[通过]", "WARN": "[警告]", "FAIL": "[失败]"}[level]
    print(f"{marker} {name}: {detail}")


def command_output(command: list[str], *, cwd: Path | None = None) -> tuple[int, str]:
    try:
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    output = (result.stdout + "\n" + result.stderr).strip()
    return result.returncode, output


def find_source(explicit: str | None) -> Path:
    script_dir = Path(__file__).resolve().parent
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend(
        [
            script_dir.parent.parent,
            script_dir.parent / "TGA-TeamGipsyAgent",
            Path.cwd() / "TGA-TeamGipsyAgent",
            Path.cwd(),
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "tga3" / "pyproject.toml").is_file() and (resolved / "apps" / "web" / "package.json").is_file():
            return resolved
    raise SystemExit("未找到源码目录。请使用 --source-dir 指向 TGA-TeamGipsyAgent。")


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("顶层必须为 JSON 对象")
        record("PASS", path.name, "JSON 语法正确")
        return value
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        record("FAIL", path.name, str(exc))
        return None


def check_files(source: Path) -> dict[str, dict[str, Any] | None]:
    print("\n一 文件与配置")
    required = (
        "tga3/pyproject.toml",
        "tga3/compose.yaml",
        "tga3/schema.sql",
        "tga3/docker/Dockerfile.base",
        "tga3/docker/Dockerfile.openai",
        "tga3/docker/Dockerfile.claude",
        "apps/web/package.json",
        "apps/web/package-lock.json",
    )
    missing = [relative for relative in required if not (source / relative).is_file()]
    if missing:
        record("FAIL", "必需源码文件", "缺少 " + ", ".join(missing))
    else:
        record("PASS", "必需源码文件", f"已找到 {len(required)} 个关键文件")

    config_dir = source / "tga3" / "config"
    configs = {
        name.removesuffix(".json"): load_json(config_dir / name)
        for name in ("models.json", "agents.json", "scenes.json", "runtime.json")
    }
    return configs


def check_config(configs: dict[str, dict[str, Any] | None], require_api_key: bool, source: Path) -> None:
    models = configs.get("models")
    agents = configs.get("agents")
    scenes = configs.get("scenes")
    runtime = configs.get("runtime")
    if any(value is None for value in (models, agents, scenes, runtime)):
        return

    bindings = agents.get("agents", {})
    required_agents = {"scout", "worker-openai", "worker-claude", "worker-opencode", "worker-qwen", "reporter"}
    missing_agents = sorted(required_agents - set(bindings)) if isinstance(bindings, dict) else sorted(required_agents)
    record(
        "FAIL" if missing_agents else "PASS",
        "Agent 配置",
        "缺少 " + ", ".join(missing_agents) if missing_agents else "Scout、四个 Worker、Reporter 均存在",
    )

    required_scenes = {
        "penetration_test",
        "reverse_engineering",
        "pwn",
        "security_misc",
        "cryptography",
        "ai_security",
        "aoe",
    }
    scene_ids = {item.get("id") for item in scenes.get("scenes", []) if isinstance(item, dict)}
    missing_scenes = sorted(required_scenes - scene_ids)
    record(
        "FAIL" if missing_scenes else "PASS",
        "场景配置",
        "缺少 " + ", ".join(missing_scenes) if missing_scenes else "当前七类场景均存在",
    )

    # Reuse production validation rather than maintaining another provider schema.
    sys.path.insert(0, str(source / "tga3" / "src"))
    try:
        from tga3.config import TGA3Config
    except ImportError:
        record("WARN", "配置结构", "完整校验需要安装控制依赖；当前仅检查 JSON、角色和场景")
    else:
        try:
            TGA3Config(source / "tga3" / "config")
        except Exception as exc:
            record("FAIL", "配置结构", f"当前运行时拒绝配置（{type(exc).__name__}）；请在配置中心检查绑定")
        else:
            record("PASS", "配置结构", "已通过项目实际配置校验")

    providers = {item.get("id"): item for item in models.get("providers", []) if isinstance(item, dict)}
    used = {b.get("provider_id") for b in bindings.values() if isinstance(b, dict) and b.get("role") != "supervisor"}
    missing_keys = []
    for provider_id in sorted(used - {None}):
        provider = providers.get(provider_id, {})
        selected = provider.get("selected_api_key_id")
        if not any(
            k.get("id") == selected and bool(str(k.get("api_key", "")).strip())
            for k in provider.get("api_keys", [])
            if isinstance(k, dict)
        ):
            missing_keys.append(provider_id)
    record(
        "FAIL" if require_api_key and missing_keys else "WARN" if missing_keys else "PASS",
        "API 密钥",
        "实际绑定 Provider 尚未配置密钥: " + ", ".join(missing_keys)
        if missing_keys
        else "实际绑定 Provider 均已配置密钥（不显示内容）",
    )

    models_path = source / "tga3" / "config" / "models.json"
    if os.name != "nt" and models_path.exists():
        mode = stat.S_IMODE(models_path.stat().st_mode)
        if mode & 0o077:
            record("WARN", "密钥文件权限", f"models.json 当前权限为 {mode:o}，建议设置为 600")
        else:
            record("PASS", "密钥文件权限", f"models.json 权限为 {mode:o}")


def check_tools() -> bool:
    print("\n二 软件环境")
    record(
        "PASS" if sys.version_info >= (3, 11) else "FAIL",
        "Python",
        platform.python_version(),
    )
    docker_ok = False
    tool_commands = (
        ("node", "Node.js", "node"),
        ("npm", "npm", "npm.cmd" if os.name == "nt" else "npm"),
        ("docker", "Docker CLI", "docker"),
    )
    for lookup, name, command in tool_commands:
        path = shutil.which(lookup)
        if path:
            code, output = command_output([command, "--version"])
            first = output.splitlines()[0] if output else path
            level = "PASS" if code == 0 else "FAIL"
            if lookup == "node" and code == 0:
                try:
                    major = int(first.lstrip("v").split(".", 1)[0])
                    if major < 18:
                        level = "FAIL"
                        first += "，需要 18 或更高版本"
                except ValueError:
                    level = "WARN"
                    first += "，无法解析版本号"
            record(level, name, first)
        else:
            record("FAIL", name, "未找到命令")

    if shutil.which("docker"):
        code, output = command_output(["docker", "info"])
        docker_ok = code == 0
        record("PASS" if docker_ok else "FAIL", "Docker daemon", "可访问" if docker_ok else output[-300:])
        code, output = command_output(["docker", "compose", "version"])
        record("PASS" if code == 0 else "FAIL", "Docker Compose", output.splitlines()[0] if output else "不可用")
    if platform.system() == "Linux":
        bash_path = shutil.which("bash")
        record("PASS" if bash_path else "FAIL", "Bash", bash_path or "未找到命令")
    return docker_ok


def check_build_and_runtime(source: Path, configs: dict[str, dict[str, Any] | None], preflight: bool) -> None:
    print("\n三 构建产物与容器")
    project_dir = source / "tga3"
    venv_python = project_dir / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    dist_index = source / "apps" / "web" / "dist" / "index.html"
    if venv_python.is_file():
        record("PASS", "Python 虚拟环境", str(venv_python))
    else:
        record("WARN" if preflight else "FAIL", "Python 虚拟环境", "尚未创建")
    if dist_index.is_file():
        record("PASS", "前端构建", str(dist_index))
    else:
        record("WARN" if preflight else "FAIL", "前端构建", "缺少 dist/index.html")

    code, output = command_output(["docker", "compose", "config"], cwd=project_dir)
    record("PASS" if code == 0 else "FAIL", "Compose 配置", "可解析" if code == 0 else output[-300:])

    if preflight:
        return

    code, health = command_output(
        [
            "docker",
            "inspect",
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            "tga3-postgres",
        ]
    )
    record(
        "PASS" if code == 0 and health.strip() == "healthy" else "FAIL",
        "PostgreSQL 容器",
        health.strip() if output_or(health) else "未找到",
    )

    runtime = configs.get("runtime") or {}
    image_map = runtime.get("worker_images", {})
    images = ["tga3-ctf-base:local"]
    if isinstance(image_map, dict):
        images.extend(str(value) for value in image_map.values())
    for image in dict.fromkeys(images):
        code, output = command_output(["docker", "image", "inspect", image])
        record("PASS" if code == 0 else "FAIL", f"镜像 {image}", "存在" if code == 0 else output[-200:])


def output_or(value: str) -> bool:
    return bool(value and value.strip())


def http_get(url: str, *, expect_json: bool = False) -> tuple[bool, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "TGA3-Deployment-Validator/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            body = response.read(1024 * 1024)
            status = response.status
            if not 200 <= status < 400:
                return False, f"HTTP {status}"
            if expect_json:
                data = json.loads(body.decode("utf-8"))
                if data.get("status") != "ok" or data.get("service") != "tga3":
                    return False, f"响应内容异常: {data}"
            return True, f"HTTP {status}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return False, str(exc)


def check_http(api_url: str, frontend_url: str | None) -> None:
    print("\n四 HTTP 服务")
    health_url = api_url.rstrip("/") + "/api/v3/health"
    ok, detail = http_get(health_url, expect_json=True)
    record("PASS" if ok else "FAIL", "控制面健康检查", f"{health_url} {detail}")
    if frontend_url:
        ok, detail = http_get(frontend_url)
        record("PASS" if ok else "FAIL", "前端首页", f"{frontend_url} {detail}")
    else:
        record("WARN", "前端首页", "已按参数跳过")


def summary() -> int:
    counts = {level: sum(item.level == level for item in RESULTS) for level in ("PASS", "WARN", "FAIL")}
    print("\n验证汇总")
    print(f"通过 {counts['PASS']} 项，警告 {counts['WARN']} 项，失败 {counts['FAIL']} 项。")
    if counts["FAIL"]:
        print("部署验证未通过。请按仓库根 README.md 检查失败项。")
        return 1
    if counts["WARN"]:
        print("未发现阻断性错误，但上线前应处理警告项。")
    else:
        print("部署验证通过。")
    return 0


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="只读验证 TGA3 部署")
    parser.add_argument("--source-dir", help="TGA-TeamGipsyAgent 源码目录")
    parser.add_argument("--api-url", default="http://127.0.0.1:8083", help="控制面根地址")
    parser.add_argument("--frontend-url", default="http://127.0.0.1", help="前端首页地址")
    parser.add_argument("--skip-frontend", action="store_true", help="跳过前端 HTTP 检查")
    parser.add_argument("--preflight", action="store_true", help="只检查源码、配置和软件，不要求服务已启动")
    parser.add_argument("--require-api-key", action="store_true", help="将实际绑定 Provider 的空密钥视为失败")
    args = parser.parse_args()

    source = find_source(args.source_dir)
    print("TGA3 部署验证")
    print(f"源码目录: {source}")
    configs = check_files(source)
    check_config(configs, args.require_api_key, source)
    docker_ok = check_tools()
    if docker_ok:
        check_build_and_runtime(source, configs, args.preflight)
    else:
        record("FAIL", "容器与构建检查", "Docker daemon 不可用，无法继续")
    if not args.preflight:
        check_http(args.api_url, None if args.skip_frontend else args.frontend_url)
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
