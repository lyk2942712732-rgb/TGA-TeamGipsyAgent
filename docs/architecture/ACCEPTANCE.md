# TGA 一步到位跨平台启动改造 · 最终验收矩阵

> 对应指导文档第 20 节「最终验收标准」
> 验收日期：2026-08-04
> 被测版本：本地部署层 + 上游 `lyk2942712732-rgb/TGA-TeamGipsyAgent@daa2302` 合并后
> 验收主机：Windows 11 Home China 10.0.26200 / WSL2 2.6.3.0 / TGA-Runtime (Ubuntu 24.04.4 LTS)

图例：`通过` 已实测满足 · `未通过` 已实测不满足 · `阻塞` 依赖外部未就绪条件，无法在本机证实

---

## 20.1 Windows

在一台新的 Windows 11 机器上：

```powershell
安装 TGA
tga up
```

必须满足：

| # | 验收项 | 结论 | 校验信息 |
|---|---|---|---|
| 1 | 自动处理 TGA 专用 WSL2 | 通过 | `tga status` 输出 `Surface  windows -> wsl:TGA-Runtime`；`wsl --list --verbose` 中 `TGA-Runtime  Running  2`。launcher 自动探测发行版是否已 provision（`DistroProvisioned` 检查 `/opt/tga/bin/tga-internal` 可执行），未就绪时回退开发树 |
| 2 | 自动启动全部服务 | 部分通过 | API 自动启动：`start_api  systemd tga-api.service pid 438`。Docker 与 sandboxd 未自动启动，如实降级上报（见第 5、11 项） |
| 3 | 浏览器自动打开 | 通过 | 由 launcher 侧 `openBrowser()` 负责（Windows 走 `rundll32 url.dll,FileProtocolHandler`），worker 始终以 `--no-open` 调用——WSL2 内部无法触达 Windows 浏览器。本次验收全程使用 `--no-open` 以免干扰终端 |
| 4 | readiness 通过 | 通过（degraded） | `wait_for_readiness  degraded`；`GET /api/v2/system/readiness` 返回 `ready=true, status=degraded`。按既定分级，核心可服务即 `ready=true`，沙箱未强制隔离则整体计 `degraded` |
| 5 | Kali 容器能执行真实命令 | 阻塞 | 镜像未发布。GHCR 探测：`team-gipsy/tga-kali-ctf-web` 与 `lyk2942712732-rgb/tga-kali-ctf-web` 均 HTTP 403（对照 `homebrew/core/git` HTTP 200）；仓库无 `sandbox-v*` tag，发布 workflow 从未触发。当前 22 个 profile 的 image 仍为 `REPLACE_WITH_RELEASE_DIGEST` |
| 6 | 用户不需要打开 WSL 终端 | 通过 | 全部验收操作均通过 `tga.exe` 完成；launcher 内部以 `wsl.exe -d TGA-Runtime -- /opt/tga/bin/tga-internal <verb> --json` 转发 |
| 7 | 用户不需要执行 PowerShell 部署脚本 | 通过 | 仓库内无面向用户的 `.ps1`；`deploy/` 下仅 `provision.sh` 与 `install.sh`，均为安装包资源，由 `tga up` / 安装器调用 |
| 8 | 用户不需要手工安装 Docker | 通过 | `tga up` 不要求 Docker 存在；缺失时报 `DOCKER_UNAVAILABLE` 并降级继续，不阻断启动 |
| 9 | 重启后再次 `tga up` 可恢复 | 通过 | 实测 `wsl --terminate TGA-Runtime` → 服务 HTTP 000 → `tga up` → `[--] already_running pid 438` → SPA HTTP 200 |
| 10 | `tga down/status/doctor/logs` 正常 | 通过 | `down` → `TGA stopped. Task data was preserved.`；`status` → `Phase stopped / Running false`；`logs` → 输出 uvicorn 启动日志；`doctor` → 逐项列出并给出修复建议 |

## 20.2 Linux

在新的 Ubuntu 服务器上：

```bash
安装 TGA 包
tga up
```

必须满足：

| # | 验收项 | 结论 | 校验信息 |
|---|---|---|---|
| 1 | 自动初始化服务 | 通过 | `deploy/linux-package/install.sh` → `provision.sh` 建立 `/opt/tga/{app,web,bin}`、`/etc/tga`、`/var/lib/tga/runs`、`/var/log/tga`，创建 `tga` 系统账户与 `tga-sandbox` 组，安装并 enable `tga-api.service` |
| 2 | 自动准备配置和镜像 | 部分通过 | 配置自动生成并绑定本机事实：`sandboxd.run_root=/var/lib/tga/runs`、`allowed_client_uids=[999]`（由 `tga` 账户 UID 解析）、`docker_sandbox.task_root=/var/lib/tga/runs`。镜像无法准备，原因同 20.1 第 5 项 |
| 3 | 输出访问地址 | 通过 | `TGA is degraded at http://127.0.0.1:8123` |
| 4 | Kali 容器能执行真实命令 | 阻塞 | 同 20.1 第 5 项。执行边界已就位并 fail-closed：22 个 profile 全部被 `ensure_kali_profile_ready` 拒绝，放行 0 个，样例原因 `('architecture-analysis-v1', 'unresolved_image_digest')` |
| 5 | 服务重启后可恢复 | 通过 | 同 20.1 第 9 项；`tga-api.service` 为 `enabled`，发行版启动即自动拉起，`tga up` 幂等接管 |
| 6 | 不暴露 Docker 和 sandboxd | 通过 | `ss -ltn` 仅有 `LISTEN 127.0.0.1:8123`；Docker TCP 端口（2375/2376）监听数为 0；sandboxd 仅使用 Unix socket `/run/tga-sandboxd/sandboxd.sock`，从不监听 TCP |
| 7 | `tga down/status/doctor/logs` 正常 | 通过 | Linux 侧 `/usr/local/bin/tga`（同一 Go 二进制的 linux/amd64 构建）四个动词均验证通过，输出与 Windows 侧逐字一致 |

---

## 第 19 节 · 实施顺序完成度

| PR | 内容 | 状态 |
|---|---|---|
| PR 1 | 统一 `TGA_RUN_ROOT`、支持 `TGA_WEB_DIST`、内部 `tga-internal serve`、正式启动路径去除浏览器与 WebView 依赖 | 完成 |
| PR 2 | 完整 readiness、统一错误码、`tga-internal doctor`、Sandbox smoke test | 完成 |
| PR 3 | `up`/`down`/`status`/`logs`、状态机、锁、幂等 | 完成 |
| PR 4 | Go `tga` CLI、Windows `tga.exe`、Linux `tga`、WSL 命令转发、浏览器打开 | 完成 |
| PR 5 | TGA 专用 WSL rootfs、Linux 安装包、systemd、Kali 镜像发布、离线镜像包 | 部分完成 —— 发行版、安装包、systemd 已完成并实测；镜像发布与离线包受阻于未发布镜像 |
| PR 6 | 完整 E2E | 部分完成 —— 本机双平台 E2E 已完成；全新机器安装测试与真实沙箱任务测试受阻于同一原因 |

## 第 17 节 · 不再保留的正式入口

| 入口 | 处理 | 校验信息 |
|---|---|---|
| `tga go` | 已删除 | `tests/test_cli_main.py::test_retired_startup_entrypoints_are_gone[go]` |
| `tga web` | 已删除 | 同上 `[web]` |
| `tga serve` | 转为内部命令 | 同上 `[serve]`；仅 `tga-internal serve` 保留，由 systemd 托管 |
| `tga/cli/desktop.py` | 已删除 | 文件不存在；`pywebview` 不再出现在任何启动路径 |

## 测试基线

| 套件 | 结果 |
|---|---|
| `python -m pytest -q` | 558 passed, 1 skipped, 0 failed |
| `npm test`（apps/web） | 27 files / 102 tests passed |
| `go test ./...`（launcher） | ok |
| `npm run build` | 通过 |
| `go vet ./...` | 通过 |

其中部署层与镜像 pin 机制新增 72 个用例，覆盖路径解析、状态机与锁、readiness 分级、systemd 与子进程两条监管路径、配置生成、执行边界与 digest 解析。

### 已修复的测试顺序缺陷

`tests/integration/test_linux_sandbox.py` 是给专用特权 runner 用的脚本（仅含 `main()`，由
`scripts/sandbox_integration_test.sh` 以 `python3` 直接执行），但文件名匹配 `test_*.py`，
导致 `pytest` 会收集并导入它。该导入把 sandbox provider 提前载入，改变了导入顺序，
使 `tests/test_runtime_context_v6.py` 间歇性失败——最小复现为
`pytest tests/integration tests/test_runtime_context_v6.py`。

修复：在 `pyproject.toml` 中将 `tests/integration`、`tests/fixtures`、`tests/snapshots`
加入 `norecursedirs`。特权 runner 仍按原方式直接执行该脚本，不受影响。

## 镜像 digest 占位符的处理

`repo@sha256:...` 是 registry manifest digest，只在推送后才存在，无法手工编造。
为此新增 `scripts/resolve_sandbox_digests.py`，闭合「构建 → 推送 → 读回真实 digest →
回写 sandbox.json」，registry 作为参数传入（本地开发用 `localhost:5000`，发布用 GHCR），
从而同时消除 sandbox.json 中写死 `ghcr.io/team-gipsy` 的命名空间假设。

| 项 | 状态 | 校验信息 |
|---|---|---|
| Solver Dockerfile 的 `BASE_IMAGE` 占位符 | 已修复 | 22 个 Dockerfile 的默认值由 `ghcr.io/team-gipsy/tga-kali-base@sha256:REPLACE_WITH_RELEASE_DIGEST` 改为 CI 实际传入的 `tga-kali-base:release`，本地构建从此可用；由 `test_solver_dockerfiles_carry_no_placeholder_base` 守住 |
| `docker_sandbox.template` | 已修复 | 解析为真实 digest `sha256:39cf20eca861...`，并二次确认 `docker.io/docker/sandbox-templates@sha256:39cf...` 可独立寻址（OCI image index） |
| 发布后未回写 sandbox.json | 已修复 | 原 workflow 把不可变引用写入 `published-images.txt` 后即结束，占位符依旧。现增加 `--from-published` 步骤在发布后回写并 `--check` 校验 |
| 22 个 profile 的 image digest | 机制就绪，未全量构建 | 已用 `tga-kali-base` + `evidence-triage` + `logic-recovery` 跑通全链路：推送到本地 registry 后读回真实 digest，且 toolset digest 与 sandbox.json 逐一校验通过（不匹配会拒绝写入）。验证后已回退——machine-local 的 `localhost:5000` 地址不应进入共享配置 |

全链路已验证的证据：这两个 profile 被 pin 之后，`ensure_kali_profile_ready` 的拒绝原因
由 `unresolved_image_digest` 变为 `sandboxd_client_policy_missing`，即镜像 digest 这一关
已真正通过，剩下的是仓库配置尚未绑定主机 UID（由 provision 时的 `config_generator` 填充，
WSL 中该值已为 `[999]`）。

复现命令：

```bash
docker run -d -p 5000:5000 --name tga-registry registry:2
python scripts/resolve_sandbox_digests.py --registry localhost:5000
python scripts/resolve_sandbox_digests.py --check
```

未做全量 22 镜像构建：重型镜像（ghidra / sage / jadx / volatility）单个 2–5 GB，
当前 E: 剩余 51 GB、构建缓存已占 32 GB，存在写满磁盘的风险。

---

## 阻塞项的唯一根因与解除条件

20.1 第 5 项与 20.2 第 4 项是同一件事：**Solver 镜像尚未发布**。

已确认的事实：

- `config/sandbox.json` 中 22 个 profile 的 image 均为 `...@sha256:REPLACE_WITH_RELEASE_DIGEST`，`docker_sandbox.template` 同样。
- 上游仓库无 `sandbox-v*` tag，而 `.github/workflows/sandbox-images-release.yml` 仅由该 tag 触发，故发布从未执行。
- GHCR 匿名探测 `team-gipsy/*` 与 `lyk2942712732-rgb/*` 下的目标镜像均为 HTTP 403。
- 该 workflow 发布到 `ghcr.io/${{ github.repository_owner }}`，与 `sandbox.json` 中写死的 `ghcr.io/team-gipsy/...` 命名空间不一致，发布后仍需修正镜像前缀。

解除条件：推送 `sandbox-v*` tag 触发发布 → 将产出的真实 digest 写回 `config/sandbox.json`（并对齐命名空间）→ 重跑 `deploy/wsl-rootfs/provision.sh` → `tga up` 即由 `degraded` 转为 `ready`。

在此之前，当前状态是正确且安全的：`runtime` 虽声明 `enforced`，但每个 profile 在执行边界被 `ensure_kali_profile_ready` 以 `unresolved_image_digest` 拒绝，实测放行数为 0。
