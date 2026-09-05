#!/usr/bin/env python3
"""TGA3 的唯一部署编排实现。

完整部署支持具备 systemd 或其他服务管理器的通用 Linux。发行版的软件
安装方式可以不同，但项目构建和初始化步骤统一由本脚本执行。源码目录
中的旧 bootstrap 入口只负责转交到这里。
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def find_source(explicit: str | None) -> Path:
    script_dir = Path(__file__).resolve().parent
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend(
        [
            script_dir.parent,
            script_dir.parent / "TGA-TeamGipsyAgent",
            Path.cwd() / "TGA-TeamGipsyAgent",
            Path.cwd(),
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "tga3" / "pyproject.toml").is_file() and (
            resolved / "apps" / "web" / "package.json"
        ).is_file():
            return resolved
    raise SystemExit("未找到源码目录。请使用 --source-dir 指向 TGA-TeamGipsyAgent。")


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("\n> " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def run_preflight(source: Path) -> int:
    validator = Path(__file__).resolve().parent / "部署验证脚本.py"
    return subprocess.run(
        [
            sys.executable,
            str(validator),
            "--source-dir",
            str(source),
            "--preflight",
        ],
        check=False,
    ).returncode


def venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def deploy(
    source: Path,
    *,
    skip_images: bool,
    kali_mirror: str | None,
    build_network: str | None,
) -> None:
    if platform.system() != "Linux":
        raise SystemExit(
            "完整部署仅支持 Linux。Windows 和 macOS 可使用 --check-only；"
            "生产构建请在 Linux 服务器执行。"
        )

    project_dir = source / "tga3"
    web_dir = source / "apps" / "web"
    build_script = project_dir / "scripts" / "build-images.sh"

    print("TGA3 统一 Linux 部署")
    print(f"源码目录: {source}")
    print("部署编排来源: 部署手册/deploy.py")

    print("\n[1/4] 启动并等待 PostgreSQL")
    run(["docker", "compose", "up", "-d", "--wait", "postgres"], cwd=project_dir)

    print("\n[2/4] 构建 Worker 镜像")
    if skip_images:
        print("已跳过镜像构建。创建真实任务前必须保证 runtime.json 指定的镜像存在。")
    else:
        environment = os.environ.copy()
        if kali_mirror:
            environment["KALI_MIRROR"] = kali_mirror
        if build_network:
            environment["DOCKER_BUILD_NETWORK"] = build_network
        run(["bash", str(build_script)], cwd=project_dir, env=environment)

    print("\n[3/4] 创建虚拟环境并安装控制面")
    venv_dir = project_dir / ".venv"
    python_bin = venv_python(venv_dir)
    if not python_bin.is_file():
        run([sys.executable, "-m", "venv", str(venv_dir)])
    run([str(python_bin), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python_bin), "-m", "pip", "install", ".[control]"], cwd=project_dir)

    print("\n[4/4] 安装依赖并构建前端")
    run(["npm", "ci"], cwd=web_dir)
    run(["npm", "run", "build"], cwd=web_dir)
    if not (web_dir / "dist" / "index.html").is_file():
        raise RuntimeError("前端构建未生成 apps/web/dist/index.html")

    print("\nDeployment complete")
    print("启动控制面后，请在前端配置中心填写 Provider、模型、API 密钥和 Agent 绑定。")
    print(f"控制面命令: {python_bin} -m tga3.cli --config-dir {project_dir / 'config'} serve")
    print("生产服务安装、账户权限和 Nginx 配置请继续按生产环境部署.md执行。")


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="TGA3 统一 Linux 部署")
    parser.add_argument("--source-dir", help="TGA-TeamGipsyAgent 源码目录")
    parser.add_argument("--check-only", action="store_true", help="仅运行统一部署预检")
    parser.add_argument("--skip-images", action="store_true", help="更新或控制面开发时跳过 Worker 镜像构建")
    parser.add_argument("--kali-mirror", help="传给镜像构建的 KALI_MIRROR")
    parser.add_argument(
        "--build-network",
        choices=("host", "bridge", "default", "none"),
        help="传给镜像构建的 DOCKER_BUILD_NETWORK",
    )
    args = parser.parse_args()

    source = find_source(args.source_dir)
    preflight_code = run_preflight(source)
    if args.check_only or preflight_code != 0:
        return preflight_code

    deploy(
        source,
        skip_images=args.skip_images,
        kali_mirror=args.kali_mirror,
        build_network=args.build_network,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n部署已由用户中止。", file=sys.stderr)
        raise SystemExit(130)
    except subprocess.CalledProcessError as exc:
        print(f"\n命令执行失败，退出码 {exc.returncode}。", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
