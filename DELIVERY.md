# TGA 一步到位跨平台启动改造 · 交付说明

> 交付日期：2026-08-04
> 依据文档：《TGA 一步到位跨平台启动改造指导》
> 基线：上游 `lyk2942712732-rgb/TGA-TeamGipsyAgent@daa2302` 合并后
> 验证主机：Windows 11 (10.0.26200) · WSL2 2.6.3.0 · TGA-Runtime (Ubuntu 24.04.4 LTS)

---

## 1. 一句话总结

TGA 从「`tga go` / `tga web` 双入口 + 手工部署脚本」改造为**单一 `tga up` 跨平台入口**。
Windows 上 `tga.exe` 自动管理专用 WSL2 发行版并转发命令，Linux 上 `tga` 直接调用同一套
Runtime，两端命令与输出逐字一致。用户不再需要接触 WSL、systemd、Docker 或任何部署脚本。

---

## 2. 解决的问题

### 2.1 P0：run_root 分裂（数据与沙箱回收不在同一棵树）

`apps/api/main.py` 硬编码 `SandboxLifecycleService("runs")`，而 API 其余部分从
`TGA_RUN_ROOT` 读取。当部署配置为 `TGA_RUN_ROOT=/var/lib/tga/runs` 时，任务写入一个根、
沙箱回收扫描另一个根，导致孤儿容器泄漏且永不清理。

同类硬编码共 6 处：`apps/api/main.py`、`apps/api/routes/support.py`、
`tga/bootstrap/container.py`、`tga/sandbox/lifecycle.py`、`tga/skills/store.py`、
`tga/tools/mcp_config.py`（另有 `team_runtime.py` 的 skill corpus 路径）。

**修复**：全部收敛到 `tga/deployment/paths.py::run_root()`，解析顺序为
显式参数 → `TGA_RUN_ROOT` → 开发态 `runs`，且结果恒为绝对路径（避免后续 `chdir` 悄悄改指向）。
由 `test_every_consumer_agrees_on_one_run_root` 守住回归。

### 2.2 启动成功的判据不可信

原先只有 `/api/health`，它仅能证明「有进程在监听」，不能证明存储可写、更不能证明工具执行已被隔离。

**修复**：新增 `GET /api/v2/system/readiness`，按能力分级：

| 状态 | 含义 | `tga up` |
|---|---|---|
| `ready` | 核心可用且沙箱隔离已强制 | 成功 |
| `degraded` | 核心可服务，但隔离未强制 | 成功，并打印缺哪一项 |
| `failed` | API 或存储不可用 | 失败 |

`degraded` 是真实状态而非四舍五入：沙箱 `disabled` 或镜像未 digest 固定时，
界面可用但**绝不谎称**具备隔离能力。

### 2.3 缺少可操作的失败信息

**修复**：20 个稳定错误码（`tga/deployment/errors.py`），每个都带修复建议。
错误码通过 JSON 跨越 WSL 边界，因此 WSL2 内部的失败在 Windows 终端仍保有身份。

### 2.4 `tga down` 停不掉 systemd 托管的服务

实测发现：systemd 存在时，`tga down` 杀掉记录的 PID 后 systemd 会立刻重启它；
反之外部执行 `systemctl stop` 后 `tga status` 仍显示运行中。

**修复**：`tga/deployment/service_manager.py` —— 谁启动的谁停止。systemd 存在且单元已安装时，
`up` 调 `systemctl start`、`down` 调 `systemctl stop`、`status` 以 systemctl 为准；
否则由 launcher 自行监管子进程。

### 2.5 锁的陈旧判定会误判

死 PID 应立即回收，但**空/损坏**的锁不能立即回收：加锁是「创建文件」+「写 PID」两步，
中间窗口读到空内容会让两个进程同时持锁。

**修复**：命名了 owner 的锁在 owner 消失时立即回收；无法读出 owner 的锁需等待
`STALE_GRACE_SECONDS`（30 秒）后才回收。

### 2.6 镜像 digest 占位符

`repo@sha256:...` 是 **registry manifest digest，只在推送后才存在，无法手工编造**，
所以占位符不能靠编辑解决。此外发现三个具体缺陷：

1. 22 个 solver Dockerfile 的 `ARG BASE_IMAGE` 默认值是占位符 digest，本地构建直接失败
   （CI 因显式传参而未暴露）。
2. 发布 workflow 把不可变引用写入 `published-images.txt` 后即结束，**从不回写 sandbox.json**
   —— 即使发布成功，占位符依旧存在。
3. workflow 发布到 `ghcr.io/${{ github.repository_owner }}`，而 sandbox.json 写死
   `ghcr.io/team-gipsy/...`，命名空间不一致。

**修复**：见第 4 节。

### 2.7 间歇失败的测试

`tests/integration/test_linux_sandbox.py` 只含 `main()`，是给专用特权 runner 用
`python3` 直接执行的脚本，但文件名匹配 `test_*.py`，使 pytest 收集并导入它，
提前载入 sandbox provider 改变导入顺序，导致 `test_runtime_context_v6.py` 间歇失败。

最小复现：`pytest tests/integration tests/test_runtime_context_v6.py`

**修复**：`pyproject.toml` 增加 `norecursedirs`。特权 runner 的调用方式不受影响。

### 2.8 CI `images` job 长期失败：dynamic-fuzzing-solver 无法构建

`sandbox-runtime / images` 在 main 上连续失败，可追溯至 2026-08-01（最近 8 次运行
全部失败），唯一卡点是 `containers/kali/solvers/dynamic-fuzzing-solver`。
逐层排查出五个相互独立的原因：

| # | 原因 | 修复 |
|---|---|---|
| 1 | `fuzz-tools` 构建阶段以非 root 运行（base 镜像以 `USER 10001:10001` 结尾），`apt-get` 报 `List directory /var/lib/apt/lists/partial is missing - Permission denied` | 该阶段补 `USER root`（最终阶段本来就有，唯独构建阶段漏了） |
| 2 | honggfuzz 2.6 早于 GCC 15 的 `-Wunterminated-string-initialization`，撞上其自带 `-Werror` | 经**环境变量**注入 `-Wno-error=unterminated-string-initialization`；其余警告仍为致命 |
| 3 | `BUILD_LINUX_NO_BFD=true` 在 2.6 中只加 `-D` 宏，链接行仍无条件带 `-lopcodes -lbfd` | 构建阶段加装 `binutils-dev` |
| 4 | radamsa 的 `get-owl` 用 `curl` 拉取 owl-lisp，但镜像未装 curl | 加装 `curl` |
| 5 | `aoh/owl-lisp` 仓库已改名为 `owl`，GitHub 归档根目录变成 `owl-$VER`，而 radamsa v0.5 硬编码 `cd owl-lisp-$VER`；且 owl-lisp 与 radamsa 均早于 C23，GCC 15 默认 `-std=gnu23` 拒绝 `word vm();` | 按预期路径 clone owl-lisp 并校验 commit（跳过损坏的下载逻辑），以**命令行变量**传入 `-std=gnu17` |

两处 CFLAGS 机制不可互换，这是本修复中最容易踩错的一点：honggfuzz 的 Makefile 对
CFLAGS 做 `+=` 追加，命令行变量会整体覆盖它、连 `-I.` 都丢掉，必须走环境变量；
radamsa 的 Makefile 对 CFLAGS 做硬赋值，环境变量无效，必须走命令行变量
（命令行变量还会经 MAKEFLAGS 传递给嵌套的 owl 构建）。

**验证**：镜像完整构建成功，并通过 CI 使用的同一个契约校验
`scripts/validate_kali_image.py --image tga-kali-dynamic-fuzzing:pr --profile dynamic-fuzzing-v1`
（exit 0）——即以 `10001:10001` 运行、toolset digest 与 `sandbox.json` 一致、
`python3` / `afl-fuzz` / `clang` / `honggfuzz` / `radamsa` 五个声明可执行文件
在 `--network none --read-only --cap-drop ALL` 容器内实测存在、无 `sudo`、apt 列表已清空。
CI 亦确认该镜像已构建通过。

### 2.9 CI `images` job：Kali 的 zeek 包在当前 kali-rolling 上已无法安装

修复 2.8 后，`images` 的失败点移至 `host-network-forensics-solver`：

```
zeek : Depends: libc6 (< 2.38) but 2.42-16 is to be installed
E: Unable to satisfy dependencies.
```

Kali 源中的 `zeek 5.1.1-0kali3` 要求 `libc6 < 2.38`，而当前 kali-rolling 为 2.42，
该包已不可安装，并使整个 apt 事务失败。这是 Kali 上游打包问题，仓库内无法通过
版本固定解决。隔离验证：去掉 zeek 后其余四个包安装正常，单独安装 zeek 则失败。

**修复**：改用 Zeek 项目官方仓库（OBS `security:zeek/Debian_Testing`）安装 `zeek-core`
8.2.1，并以 `signed-by=` 限定密钥作用域、校验密钥指纹
`F9FA0223B56B116C363737EF5DA57BDD6DD785CA`。这里固定密钥比固定版本更有意义：
密钥稳定，而该仓库只保留当前发行版本。`zeek-core` 安装于 `/opt/zeek`，不在 PATH 上，
而 profile 声明的是 `zeek`、readiness 用 `shutil.which` 解析，故建立软链
`/usr/local/bin/zeek`。构建期用到的 `curl` / `gnupg` 在同一层内 purge。

### 2.10 CI `images` job 的其余六类故障

修复 2.8、2.9 后失败点依次前移，逐一排查出：

| # | 镜像 | 原因 | 修复 |
|---|---|---|---|
| 3 | malware | `flare-floss` 依赖的 `binary2strings` 只发布 Windows wheel，Linux 上必须现场编译 C++，而镜像无编译器 | 同层内装 `g++`/`python3-dev`，用完即 purge；断言编译产物可 import 且编译器已移除 |
| 4 | web-api-analyst | `graphql-cop` 钉死 `requests==2.25.1`，会把系统 `urllib3` 从 2.7.0 降级；pip 无法卸载 dpkg 装的包而报 `uninstall-no-record-file` | 独立 venv + PATH wrapper；顺带避免降级同镜像内 `sqlmap`/`wafw00f` 依赖的 urllib3 |
| 5 | timeline-ioc | Kali 重命名工具：`log2timeline.py`→`plaso-log2timeline`、`psort.py`→`plaso-psort`、`sigma`→`sigma-cli`（且 `plaso` 是无二进制的元包） | 软链保持 profile 声明的名字；加循环断言防止后续改名静默漏装 |
| 6 | code-audit | `gosec` v2.22.9 依赖 `grpc` v1.75.0，含 CVE-2026-33186 | 升 gosec v2.27.1（grpc v1.81.1）；所有修复版均要求 Go ≥ 1.25，故该镜像构建器同步升级 |
| 7 | ctf-web / surface-mapper / vulnerability-validator / web-api-analyst | `dalfox` v2.12.0 四个 CVE、`nuclei` v3.4.10 三个 CVE | 升 dalfox v2.13.0、nuclei v3.8.0，构建器升 Go 1.25 |
| 8 | web-api-analyst | `kiterunner` 发布二进制用 go1.15.11 编译，Trivy 报出此后所有 Go 标准库 CVE（4 个 CRITICAL）；项目 2021 年停更，无新版可升 | 改为用当前工具链从源码编译同一 pinned commit，产物为 go1.25.12，四个 CVE 在源头消除 |

### 无法通过升级消除的两条

`.trivyignore` 中记录两条定向豁免，每条都写明携带镜像、不可达依据、为何升级无效、以及移除条件：

- **GHSA-r277-6w6q-xmqw**（kin-openapi fail-open）—— nuclei 仅导入 `openapi2`/`openapi2conv`/`openapi3`
  三个规范解析包，对 `kin-openapi/routers` 与 `ValidationHandler` 零引用；nuclei v3.8.0～v3.11.0
  全部钉 v0.132.0，强升 v0.144.0 会因 API 变更编译失败。
- **CVE-2025-68121**（Go `crypto/tls` 证书校验）—— 位于 frida 预编译 wheel 内，
  frida 17.16.4 与最新 17.17.0 均为 go1.24.3 构建；且 `dynamic-analysis-v1` 的
  `network_mode` 为 `none`、`allow_net_raw` 为 false，无网络容器无法完成 TLS 握手。

其余任何 CRITICAL 仍会阻断扫描。

### 全量依赖体检

修复过程中改用一次性体检替代逐个构建：解析全部 22 个 Dockerfile 的 apt 包列表，
在 base 镜像内用 `apt-get install -s` 逐组模拟。结果为 **25 组中仅 1 组不可解析**
（即上述 `host-network-forensics-solver`），其余全部正常——这确认了 `images`
不是普遍性损坏，而是两个孤立故障点。

（该体检最初给出「25/25 全部失败」的错误结论，原因是包列表文件以 CRLF 写出，
容器内 `read` 将 `\r` 留在每组最后一个包名上。修正行尾后结论才成立。）

后续又补了两类体检，用于替代「CI 一轮暴露一个漏洞」的低效循环：

- `scripts/audit_go_tools.py` —— 就 8 个钉版 Go 工具逐一查询 OSV。一次性定位出
  dalfox 与 nuclei，省去数轮 CI。当前全部返回干净。
- 预编译二进制体检 —— 直接对镜像下载的每个发布产物执行 `go version`，读出其内嵌的
  Go 工具链。OSV 看不到这一层（它回答的是包的公告，不是厂商用什么编译器构建的），
  frida 与 kiterunner 都属此类。结果：仅 kiterunner 为 go1.15.11，
  trivy（go1.26.5）、syft（go1.26.3）正常，chainsaw/hayabusa/evtx_dump/casr 为
  Rust/C 产物不适用。

### CI 最终状态

`sandbox-runtime` 三个 job 全部通过——这是自 2026-08-01 以来首次：

| job | 结论 |
|---|---|
| `python` | success |
| `go` | success |
| `images` | success |

`images` 的每个步骤均通过：22 个镜像构建、22 项契约校验、Trivy CRITICAL 扫描、
SBOM 生成与产物上传。

---

## 3. 交付产物

全部位于仓库内。

### 3.1 可执行产物

| 路径 | 说明 |
|---|---|
| `dist/tga.exe` | Windows 启动器，3.7 MB 单文件，无外部依赖 |
| `dist/tga-linux-amd64` | Linux 启动器，同一份 Go 源码交叉编译 |

用户唯一入口：`tga up` / `down` / `status` / `doctor` / `logs`。

### 3.2 新增源码

**部署层 `tga/deployment/`**（8 个模块）

| 模块 | 职责 |
|---|---|
| `paths.py` | run_root / web_dist / state_dir / log_dir 的唯一真相来源 |
| `errors.py` | 20 个稳定错误码 + 修复建议 |
| `readiness.py` | 能力分级 readiness |
| `state.py` | durable 状态机 + 跨进程文件锁（幂等、可中断续跑） |
| `lifecycle.py` | `up` / `down` / `status` / `doctor` / `logs` 实现 |
| `serve.py` | 无头 API + SPA 服务（无浏览器、无 WebView、支持 SIGTERM） |
| `service_manager.py` | systemd 与子进程两条监管路径的抽象 |
| `config_generator.py` | 按主机事实生成 sandbox 配置并校验 |

**Go launcher `launcher/`**

```
launcher/
├── go.mod
├── cmd/tga/main.go                  公共入口
└── internal/
    ├── command/command.go           动词实现与渲染
    ├── protocol/result.go           与 worker 的 JSON 契约
    └── runtime/runtime.go           执行面解析 + WSL 转发 + UTF-16 解码
        └── runtime_test.go
```

**其他新增**

| 路径 | 说明 |
|---|---|
| `tga/cli/internal.py` | `tga-internal` 内部 worker，全部子命令支持 `--json` |
| `apps/api/routes/system.py` | `GET /api/v2/system/readiness` |
| `scripts/resolve_sandbox_digests.py` | 镜像 digest 解析与回写 |

### 3.3 部署资源 `deploy/`

| 路径 | 说明 |
|---|---|
| `wsl-rootfs/provision.sh` | 一次性初始化，幂等，可重复执行修复半成品安装 |
| `systemd/tga-api.service` | 服务单元，含 `NoNewPrivileges` / `ProtectSystem=strict` 等加固 |
| `linux-package/install.sh` | Linux 服务器安装器 |

产出的固定布局（Windows 的 WSL2 与 Linux 服务器完全一致）：

```
/opt/tga/{app,web,bin}    代码、预构建前端产物、tga-internal
/etc/tga/                 sandbox.json、tga.env
/var/lib/tga/runs/        任务数据（唯一 TGA_RUN_ROOT）
/var/log/tga/             组件日志
```

### 3.4 测试（新增 68 个 Python 用例 + 11 个 Go 用例）

| 文件 | 覆盖 |
|---|---|
| `tests/test_deployment_paths.py` | 路径解析、绝对化、多消费方一致性、不可写检测 |
| `tests/test_deployment_state.py` | 状态持久化、损坏容错、锁互斥、陈旧锁回收与宽限期 |
| `tests/test_deployment_readiness.py` | 三级分级、失败项归类、序列化契约 |
| `tests/test_deployment_service_manager.py` | systemd 与子进程两条监管路径 |
| `tests/test_deployment_config_generator.py` | 强制隔离的四类拒绝条件、执行边界 fail-closed |
| `tests/test_resolve_sandbox_digests.py` | digest 模式、发布清单回写、拒绝不可验证引用 |
| `launcher/internal/runtime/runtime_test.go` | UTF-16LE 解码、JSON 契约、错误码透传 |

### 3.5 文档

| 路径 | 说明 |
|---|---|
| `docs/architecture/ACCEPTANCE.md` | **按指导文档 §20 格式的逐项验收矩阵**，每项附实测校验信息 |
| `docs/architecture/DEPLOYMENT.md` | 部署架构、分层、readiness 契约、错误码、监管与幂等语义 |
| `README.md` | Quick Start 改写为 `tga up`；补充可用性分级与目录布局 |
| `DELIVERY.md` | 本文件 |

---

## 4. 镜像 digest 占位符的处理

新增 `scripts/resolve_sandbox_digests.py`，闭合
「构建 → 推送 → 读回真实 digest → 回写 sandbox.json」，registry 作为参数传入，
因此同一条命令既服务本地开发（`localhost:5000`）也服务正式发布（GHCR），
顺带消除写死的命名空间假设。

三种用法：

```bash
# 报告哪些引用仍未固定（不需要 Docker）
python scripts/resolve_sandbox_digests.py --check

# 构建并推送到指定 registry，读回真实 digest 后回写
python scripts/resolve_sandbox_digests.py --registry localhost:5000

# 从发布清单回写，不重新构建（CI 发布后使用）
python scripts/resolve_sandbox_digests.py --from-published published-images.txt
```

已修复的具体项：

| 项 | 状态 |
|---|---|
| 22 个 Dockerfile 的 `BASE_IMAGE` 占位符 | 已修复，改为 CI 实际传入的 `tga-kali-base:release` |
| `docker_sandbox.template` | 已 pin 真实 digest `sha256:39cf20eca861...`（已二次确认可独立寻址） |
| 发布后不回写 sandbox.json | 已修复，`sandbox-images-release.yml` 增加回写与 `--check` 步骤 |
| 22 个 profile 的 image digest | 机制就绪，未全量构建（见第 6 节） |

**机制已端到端验证**：用 `tga-kali-base` + `evidence-triage` + `logic-recovery` 实际构建、
推送到本地 registry、读回真实 digest 并回写，且 toolset digest 逐一校验通过
（不匹配会拒绝写入）。证据是这两个 profile 的执行门控拒绝原因由
`unresolved_image_digest` 变为 `sandboxd_client_policy_missing` —— 镜像这一关确实已通过。

验证后已将这两条回退为占位符：`localhost:5000` 是本机地址，不应进入共享配置。

---

## 5. 如何验证

### 5.1 环境准备

需要 Python 3.11+、Node.js、Go 1.24+。

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd apps\web; npm install; npm run build; cd ..\..
cd launcher; go build -o ..\dist\tga.exe .\cmd\tga; cd ..
```

### 5.2 三套测试

```powershell
.venv\Scripts\python.exe -m pytest -q          # 期望 558 passed, 1 skipped, 0 failed
cd apps\web; npm test                          # 期望 27 files / 102 tests passed
cd ..\..\launcher; go vet ./...; go test ./... # 期望 ok
```

### 5.3 启动与生命周期

```powershell
dist\tga.exe up --no-open
dist\tga.exe status
dist\tga.exe up --no-open        # 幂等：应输出 already_running，不得重复起进程
dist\tga.exe doctor
dist\tga.exe logs --lines 20
dist\tga.exe down
```

`up` 应逐步输出 7 个阶段并以 `TGA is degraded at http://127.0.0.1:8123` 收尾
（`degraded` 的原因见第 6 节）。

### 5.4 执行面切换

```powershell
$env:TGA_RUNTIME_MODE="native"; dist\tga.exe status   # Surface: windows/development
$env:TGA_RUNTIME_MODE="wsl";    dist\tga.exe status   # Surface: windows -> wsl:TGA-Runtime
```

不设该变量时：已 provision 的发行版优先，否则回退开发树。

### 5.5 readiness 契约

```powershell
curl http://127.0.0.1:8123/api/v2/system/readiness
```

应返回 `ready` / `status` / `api` / `storage` / `sandbox{runtime,...}` / `profiles` / `errors`。
注意 `/api/health` 只能证明进程在监听，不作为启动成功判据。

### 5.6 重启恢复（幂等与续跑）

```powershell
wsl --terminate TGA-Runtime
curl http://127.0.0.1:8123/            # 应不可达
dist\tga.exe up --no-open              # 应恢复
curl http://127.0.0.1:8123/            # 应 200
```

### 5.7 执行边界 fail-closed（安全性关键项）

```powershell
.venv\Scripts\python.exe -m pytest tests\test_deployment_config_generator.py -k blocks_execution -q
```

或手工逐项检查：

```python
from tga.sandbox.config import load_sandbox_config
from tga.sandbox.readiness import ensure_kali_profile_ready, KaliProfileNotReadyError

config, _ = load_sandbox_config()
allowed, refused = [], []
for profile_id, profile in sorted(config.profiles.items()):
    if profile.provider == "remote_http":
        continue
    try:
        ensure_kali_profile_ready(profile_id, config)
        allowed.append(profile_id)
    except KaliProfileNotReadyError as exc:
        refused.append((profile_id, exc.reason))

print("放行:", allowed)      # 镜像未发布时必须为 []
print("拒绝:", len(refused))  # 必须为 22
```

镜像未发布时**必须** 22 个 profile 全部被拒、放行 0 个。

### 5.8 端口暴露面

```bash
wsl -d TGA-Runtime -u root -- ss -ltn
```

应只有 `127.0.0.1:8123`；不得出现 Docker 的 2375/2376。

### 5.9 镜像 digest 状态

```powershell
.venv\Scripts\python.exe scripts\resolve_sandbox_digests.py --check
```

---

## 6. 已知限制

### 6.1 启动状态为 `degraded` 而非 `ready`

**这是正确且安全的结论，不是缺陷。**

`config/sandbox.json` 中 22 个 profile 的 image 仍为 `REPLACE_WITH_RELEASE_DIGEST`，
因为镜像尚未发布：仓库无 `sandbox-v*` tag（发布 workflow 仅由该 tag 触发），
GHCR 匿名探测 `team-gipsy/tga-kali-*` 与 `lyk2942712732-rgb/tga-kali-*` 均返回 403。

上游已将加载期强制校验移除，改为在执行边界按 profile 门控
（`tga/sandbox/readiness.py::ensure_kali_profile_ready`，由 `sandbox/manager.py` 与
`runtime/tooling/execution/backends.py` 调用）。该门控 fail-closed，
实测 22 个 profile 全部被拒、放行 0 个。

**转为 `ready` 的路径**：推送 `sandbox-v*` tag 触发发布 → workflow 现已包含回写步骤
→ 重跑 `deploy/wsl-rootfs/provision.sh`。

### 6.2 未做全量 22 镜像构建

重型镜像（ghidra / sage / jadx / volatility）单个 2–5 GB，
验证时 E: 仅剩 51 GB 而 Docker 构建缓存已占 32 GB，存在写满磁盘的风险。
机制已验证，全量构建属于 CI 任务而非本机任务。

### 6.3 Docker daemon 未运行时报 `DOCKER_UNAVAILABLE`

按设计降级继续，不阻断启动。

### 6.4 §20 验收中的两个「阻塞」项

「Kali 容器能执行真实命令」（Windows 第 5 项、Linux 第 4 项）无法在本机证实，
根因同 6.1。其余各项均已实测并在 `docs/architecture/ACCEPTANCE.md` 附证据。

---

## 7. 不再保留的入口

| 入口 | 处理 |
|---|---|
| `tga go` | 已删除 |
| `tga web` | 已删除 |
| `tga serve` | 转为内部 `tga-internal serve`，由 systemd 托管 |
| `tga/cli/desktop.py` | 已删除，`pywebview` 不再出现在任何启动路径 |

由 `tests/test_cli_main.py::test_retired_startup_entrypoints_are_gone` 守住。

`tga status` 现在同时服务两个作用域：带 task id 打印任务快照，不带则打印部署状态。
