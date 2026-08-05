# TGA 一步到位跨平台启动改造 · 最终验收矩阵

> 对应指导文档第 20 节「最终验收标准」
> 验收日期：2026-08-04；镜像发布后更新：2026-08-06
> 被测版本：本地部署层 + 上游 `lyk2942712732-rgb/TGA-TeamGipsyAgent@daa2302` 合并后
> 验收主机：Windows 11 Home China 10.0.26200 / WSL2 2.6.3.0 / TGA-Runtime (Ubuntu 24.04.4 LTS)

图例：`通过` 已实测满足 · `未通过` 已实测不满足 · `阻塞` 依赖外部未就绪条件，无法在本机证实 ·
`待实测` 外部条件已就绪，本机尚未复测

> **2026-08-06 更新**：`sandbox-v0.1.1` 已发布 23 个镜像并把真实 digest 回写进
> `config/sandbox.json`，原先唯一的阻塞根因就此消失。相关两项由 `阻塞` 改为 `待实测`
> ——发布是可证实的事实，但「在本机真实执行一条 Kali 命令」尚未复测，不能记为通过。
> 复测步骤见文末「剩余待实测项」。

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
| 5 | Kali 容器能执行真实命令 | 待实测 | 镜像已发布：`sandbox-v0.1.1` 的 [run 31026129827](https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent/actions/runs/31026129827) 构建并契约校验 22 个 Solver 镜像 + base，Trivy 无未豁免 CRITICAL，逐个生成 SPDX SBOM 并 cosign 签名（23 条 `tlog entry created`）。`config/sandbox.json` 已回写真实 digest，`resolve_sandbox_digests.py --check` 报 `runtime enforced / 22/22 pinned`。**尚未复测**：本机未重跑 provision、未拉取镜像、未执行真实命令 |
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
| 2 | 自动准备配置和镜像 | 部分通过 | 配置自动生成并绑定本机事实：`sandboxd.run_root=/var/lib/tga/runs`、`allowed_client_uids=[999]`（由 `tga` 账户 UID 解析）、`docker_sandbox.task_root=/var/lib/tga/runs`。镜像现已可获取（见 20.1 第 5 项），但**没有任何一步主动拉取**：`tga/sandbox/readiness.py` 明确不拉镜像，实际拉取发生在 `docker_provider.py` 的 `docker create`，即首次用到某 profile 时按需下载 |
| 3 | 输出访问地址 | 通过 | `TGA is degraded at http://127.0.0.1:8123`（该次验收时镜像未发布；回写 digest 后的取值待复测） |
| 4 | Kali 容器能执行真实命令 | 待实测 | 同 20.1 第 5 项。发布前的实测结论是执行边界 fail-closed 且正确：22 个 profile 全部被 `ensure_kali_profile_ready` 拒绝，放行 0 个，样例原因 `('architecture-analysis-v1', 'unresolved_image_digest')`。digest 回写后该拒绝原因应消失，尚未复测 |
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
| PR 5 | TGA 专用 WSL rootfs、Linux 安装包、systemd、Kali 镜像发布、离线镜像包 | 部分完成，见下表逐项拆解 |
| PR 6 | 完整 E2E | 部分完成 —— 本机双平台 E2E 已完成；全新机器安装测试与真实沙箱任务测试待实测（阻塞根因已消除，见文末） |

### PR 5 逐项

此前该行记为「发行版、安装包、systemd 已完成并实测」。**这个表述偏乐观**：
`provision.sh` 建目录、装 Python、装 systemd 单元确实做了并实测过，
但它不等于第 10 节要求的那个**预装 Docker / runsc / sandboxd 的发行版**。
按文档逐条拆开：

| 条目 | 状态 | 依据 |
|---|---|---|
| Kali 镜像发布（§12） | 完成 | `sandbox-v0.1.1`，23 个镜像已扫描、签名并 pin |
| 首次启动检查/拉取镜像（§12） | 完成 | `tga/deployment/image_manager.py`；`tga up --pull-images` 拉取，默认只检查并报告缺哪些 |
| Linux 安装包 + systemd 单元（§14） | 完成 | `deploy/systemd/` 下 `tga-api.service` 与 `tga-sandboxd.service` 齐备；`provision.sh` 安装 sandboxd 二进制（有预编译产物则用之，否则用 Go 现场构建），并只在二进制确实存在时才 enable 该 unit |
| 发行版预装 Docker / runsc（§6、§10） | 未实现 | `provision.sh` 只装 python3、nftables、curl、gnupg、sudo 等 |
| `TGA-Runtime.wsl.tar.zst`（§10） | 未实现 | 仓库内无任何构建它的东西 |
| 首次运行自动 `wsl --import`（§6） | 未实现 | `launcher/internal/runtime/runtime.go` 在 WSL 缺失时只提示用户自行 `wsl --install` |
| 离线镜像包（§12） | 未实现 | |

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
| `python -m pytest -q` | 560 passed, 0 failed（2026-08-06 复跑；此前记录为 558 passed / 1 skipped，差异是本次主机装有 Go 工具链故 `test_sandboxd_agrees_with_control_plane_config_digest` 不再跳过，加上占位符测试重写后净增 1 例） |
| `npm test`（apps/web） | 27 files / 102 tests passed |
| `go test ./...`（launcher） | ok |
| `npm run build` | 通过 |
| `go vet ./...` | 通过 |

其中部署层与镜像 pin 机制新增 72 个用例，覆盖路径解析、状态机与锁、readiness 分级、systemd 与子进程两条监管路径、配置生成、执行边界与 digest 解析。

### 已修复的两个测试缺陷

**其一：不该被收集的脚本被收集了。** `tests/integration/test_linux_sandbox.py` 是给专用
特权 runner 用的脚本（仅含 `main()`，由 `scripts/sandbox_integration_test.sh` 以 `python3`
直接执行），但文件名匹配 `test_*.py`，导致 `pytest` 会收集并导入它，把 sandbox provider
提前载入。修复：在 `pyproject.toml` 中将 `tests/integration`、`tests/fixtures`、
`tests/snapshots` 加入 `norecursedirs`。特权 runner 的调用方式不受影响。

**其二：`test_runtime_context_v6.py` 依赖随机顺序。** 此前把该文件的间歇失败一并归因于
上一条，是**错误的**——2026-08-06 复查发现它单独运行同样会失败（12 次中约半数），
根本不存在收集顺序可言。

真正的原因在测试自身：`list_hints` 的排序是 `ORDER BY created_at, id`
（`tga/infrastructure/persistence/repositories.py`），而该用例连续创建两个 hint、
`created_at` 相同，于是先后完全由随机 id 决定。测试却用 `list_hints(task.id)[-1]`
指代「刚创建的第二个」，约一半概率拿到第一个，把它标成 `rejected`，
于是断言里要求出现的那条 hint 反而被排除，测试失败。

修复：直接取创建调用返回的 hint，不再按位置猜。修复后连续 12 次全部通过。

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
| 22 个 profile 的 image digest | 已完成 | 2026-08-04 先用 `tga-kali-base` + `evidence-triage` + `logic-recovery` 推本地 registry 跑通机制（验证后回退，`localhost:5000` 不应进入共享配置）。2026-08-06 由 `sandbox-v0.1.1` 全量发布并回写：22 个 profile 全部 pin 到 `ghcr.io/lyk2942712732-rgb/...@sha256:...`，`--check` 报 `runtime enforced / 22/22 pinned` |
| sandbox.json 命名空间写死 `team-gipsy` | 已解决 | 无需手工改：`resolve_sandbox_digests.py` 的 `apply_published` 按镜像引用的**最后一段路径**匹配 profile，因此指向发布清单时会把 `team-gipsy` 改写为镜像实际落地的命名空间 |

机制验证阶段的证据：这两个 profile 被 pin 之后，`ensure_kali_profile_ready` 的拒绝原因
由 `unresolved_image_digest` 变为 `sandboxd_client_policy_missing`，即镜像 digest 这一关
已真正通过，剩下的是仓库配置尚未绑定主机 UID（由 provision 时的 `config_generator` 填充，
WSL 中该值已为 `[999]`）。

本地 registry 复现命令（不依赖 GHCR）：

```bash
docker run -d -p 5000:5000 --name tga-registry registry:2
python scripts/resolve_sandbox_digests.py --registry localhost:5000
python scripts/resolve_sandbox_digests.py --check
```

全量 22 镜像的构建已由 CI 完成，不在本机进行：重型镜像（ghidra / sage / jadx /
volatility）单个 2–5 GB，本机验证时 E: 仅剩 51 GB 而构建缓存已占 32 GB。

---

## 镜像构建链路的修复

`sandbox-runtime / images` 自 2026-08-01 起在 main 上连续失败（最近 8 次运行全红），
积压了九类互不相关的上游漂移。全部修复后，该 job 已恢复通过：

| # | 镜像 | 故障 |
|---|---|---|
| 1 | dynamic-fuzzing | 构建阶段以非 root 执行 `apt-get`（base 镜像以 `USER 10001` 结尾） |
| 2 | dynamic-fuzzing | honggfuzz 撞 GCC 15 的 `-Werror` 新警告；`BUILD_LINUX_NO_BFD` 未去掉 `-lbfd` 链接；radamsa 缺 `curl`；`aoh/owl-lisp` 改名致归档目录名不符；owl/radamsa 早于 C23 |
| 3 | host-network-forensics | Kali 的 zeek 要求 `libc6 < 2.38`，当前为 2.42，已不可安装 → 改用 Zeek 官方 OBS 源 |
| 4 | malware | `binary2strings` 无 Linux wheel，需现场编译 C++ |
| 5 | web-api-analyst | `graphql-cop` 钉版触发 pip 无法卸载 dpkg 包 |
| 6 | timeline-ioc | Kali 重命名 plaso/sigma 工具，契约校验报缺失 |
| 7 | code-audit | gosec → grpc CVE-2026-33186 |
| 8 | ctf-web 等 4 个 | dalfox 4 个 CVE、nuclei 3 个 CVE |
| 9 | web-api-analyst | kiterunner 发布二进制用 go1.15.11 编译，含 4 个标准库 CVE |

九类中八类为真实修复，仅两条无法通过升级消除的漏洞记入 `.trivyignore`
（kin-openapi 与 frida，均附不可达依据与移除条件）。

排查方法上，前期为「CI 一轮暴露一个问题」，后期改为三种一次性体检：
apt 依赖模拟安装（25 组）、OSV 查询全部钉版 Go 与 PyPI 包（8 + 11 个）、
以及对全部下载的预编译二进制执行 `go version` 读取内嵌工具链。

## 镜像发布（阻塞根因的解除过程）

20.1 第 5 项与 20.2 第 4 项曾是同一件事：**Solver 镜像尚未发布**。该阻塞分两层，已逐层解除。

**第一层：镜像根本无法构建。** 已解除，见上一节的九类上游漂移修复。

**第二层：从未发布。** 已解除。发布经过两次尝试：

| tag | 结果 | 说明 |
|---|---|---|
| `sandbox-v0.1.0` | 失败 | 23 个镜像中推送并扫描了 6 个，签名第 6 个时报 `getting key from Fulcio: fetching ambient OIDC credentials: invalid character 'u' looking for beginning of value` |
| `sandbox-v0.1.1` | 成功 | 23 个镜像全部推送、扫描、生成 SBOM 并签名，digest 已回写 |

第一次失败的原因值得记录，因为它是本次改造自身引入的缺陷而非上游问题：GitHub 的
`ACTIONS_ID_TOKEN_REQUEST_TOKEN` **按 step 签发**且有效期很短，而原 workflow 把
「推送 → Trivy → SBOM → 签名」23 轮塞在同一个 step 里。时间线可以坐实——该 step
15:31:54 开始，15:33 到 15:37 连续签名成功 5 次，15:38:51 第 6 次取令牌失败，
距 step 开始 6 分 57 秒；而该 job 15:08 就已启动，首次签名在 25 分钟后仍然成功，
可见令牌不按 job 计时。

修复是把签名拆成独立 step，并在该 step 开头一次性取得 Sigstore identity token
（`.github/workflows/sandbox-images-release.yml`）。23 次签名在该 step 内约耗时 2 分钟，
稳在令牌有效期内。

发布结果（[run 31026129827](https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent/actions/runs/31026129827)）：

- 构建 + 契约校验 22 个 Solver 镜像 + base：23 分 33 秒，全部通过
- Trivy（`--severity CRITICAL --ignore-unfixed`）：无未豁免的 CRITICAL
- 23 份 SPDX SBOM，23 次 cosign 签名，23 条 `tlog entry created`，零 Fulcio 错误
- `config/sandbox.json` 已回写真实 digest，`--check` 报 `runtime enforced / 22/22 pinned`

同时暴露并修复了一个更隐蔽的缺陷：该 workflow 虽然会回写 `sandbox.json`，却只把结果
作为构建产物上传，**从不带回仓库**。因此 `sandbox-v0.1.0` 即便成功，仓库里的仍是占位符，
而 `provision.sh` 正是从仓库这份播种 `/etc/tga/sandbox.json` 的——等于发布了却部署不上。
现由本次提交将 pin 好的配置带回仓库。

## 剩余待实测项

阻塞根因已消除，但下列结论尚未在本机复测，因此未记为「通过」：

1. `tga up` 是否由 `degraded` 转为 `ready`
2. Kali 容器能否执行真实命令（20.1 第 5 项 / 20.2 第 4 项）

复测步骤：

```bash
# 1. 同步配置到运行时（本机需重跑 provision）
sudo deploy/wsl-rootfs/provision.sh

# 2. 拉取要用的 profile 镜像（按需，不必全拉；23 个合计数十 GB）
docker pull ghcr.io/lyk2942712732-rgb/tga-kali-ctf-web@sha256:...

# 3. 启动并观察
tga up
```

需要注意：**没有任何一步会主动预拉镜像**。`tga/sandbox/readiness.py` 明确不拉取，
实际下载发生在 `docker_provider.py` 的 `docker create`，即首次用到该 profile 时。
因此即便配置已 pin，未拉取的 profile 仍会在 readiness 中报 `image_unverified`。
