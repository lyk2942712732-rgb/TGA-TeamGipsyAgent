# TGA 沙箱系统首次安装与配置手册

本文面向新工作空间、新开发机和新的 Linux 沙箱主机。默认按“先保持
`disabled`，完成全部检查后再切换 `enforced`”的顺序操作。

> 当前状态：控制面、`tga-sandboxd`、协议、审计模型和网络策略已经具备可编译
> 实现，但尚未在本仓库完成真实 Linux `runsc`、nftables 和 Docker Sandboxes
> 端到端认证。因此，首次部署必须先执行本文的验收步骤，不能直接用于生产任务。

## 1. 选择部署形态

| 使用场景 | 操作系统 | Provider | 必要组件 |
| --- | --- | --- | --- |
| 普通离线分析、Web MCP、本地开发 | Windows、macOS 或 Linux | Docker Sandboxes | Python 3.11、`sbx`、虚拟化能力 |
| Nmap TCP connect | 专用 Linux 主机 | `tga-sandboxd` | Docker Engine、runsc、nftables、cgroup v2 |
| SYN、Ping、受限抓包 | 专用 Linux 主机 | `tga-sandboxd` | 上述依赖及可信 `raw-network` Profile |
| 只调用远程 HTTP MCP | 任意 | `remote_http` | 不挂载本地工作区 |

生产环境建议把 Python API 服务和 `tga-sandboxd` 部署在同一台专用 Linux
主机，通过 Unix socket 通信。不要让 `tga-sandboxd` 监听 TCP，也不要把
Docker socket 暴露给 Agent、Solver 或工具容器。

## 2. 依赖清单

### 2.1 所有开发环境

- Git。
- Python 3.11 或更高版本。
- `pip` 和 Python 虚拟环境。
- 项目 Python 依赖：
  - `grpcio`
  - `protobuf`
  - `pydantic`
  - 其余依赖由 `pyproject.toml` 统一安装。
- 开发和协议生成额外需要：
  - `pytest`
  - `grpcio-tools`

安装项目：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

PowerShell 使用：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 2.2 标准 Docker Sandboxes Provider

- 已开启 CPU 虚拟化。
- Linux 需要可用的 `/dev/kvm`。
- Docker 官方 `sbx` CLI。
- Docker 账号登录状态。

Ubuntu 的官方安装方式：

```bash
curl -fsSL https://get.docker.com | sudo REPO_ONLY=1 sh
sudo apt-get install docker-sbx
sudo usermod -aG kvm "$USER"
newgrp kvm
sbx login
```

安装后检查：

```bash
sbx version
sbx diagnose
sbx policy ls --wide
```

Docker Sandboxes 当前默认拒绝未授权 HTTP/HTTPS、原始 TCP、UDP 和 ICMP。
首次部署必须检查实际生效的本地或组织策略，不能假设默认规则永远不变。

官方文档：

- <https://docs.docker.com/ai/sandboxes/>
- <https://docs.docker.com/reference/cli/sbx/>
- <https://docs.docker.com/ai/sandboxes/security/defaults/>

### 2.3 Linux 高级网络 Provider

建议使用专用 Ubuntu LTS 或等价的 systemd Linux 主机，并满足：

- `x86_64` 或 `arm64`。
- Linux 内核满足当前 gVisor 要求。
- Docker Engine。
- gVisor `runsc`。
- nftables。
- cgroup v2。
- systemd。
- Go 1.26.5，仅构建 daemon 时需要。
- 足够的磁盘、内存和 inode；沙箱镜像及日志必须单独监控容量。

基础软件：

```bash
sudo apt-get update
sudo apt-get install --yes \
  ca-certificates curl gnupg nftables uidmap
```

确认 cgroup v2：

```bash
test -f /sys/fs/cgroup/cgroup.controllers
stat -fc %T /sys/fs/cgroup
```

第二条命令应输出 `cgroup2fs`。

Docker Engine 应按照发行版对应的 Docker 官方安装文档部署，不要在生产主机
混用发行版旧版 Docker 包和 Docker 官方仓库：

<https://docs.docker.com/engine/install/>

## 3. 安装并验证 gVisor

优先使用 gVisor 官方 apt 仓库：

```bash
sudo apt-get install --yes apt-transport-https ca-certificates curl gnupg
curl -fsSL https://gvisor.dev/archive.key \
  | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
  | sudo tee /etc/apt/sources.list.d/gvisor.list >/dev/null
sudo apt-get update
sudo apt-get install --yes runsc
```

如果安装包没有自动配置 Docker：

```bash
sudo runsc install
sudo systemctl restart docker
```

验证 Docker 能看到并真正运行 `runsc`：

```bash
docker info --format '{{json .Runtimes}}'
docker run --rm --runtime=runsc hello-world
docker run --rm --runtime=runsc ubuntu dmesg
```

`dmesg` 输出可用于人工冒烟检查，但不能作为安全认证依据；生产验收还必须检查
Docker inspect 中的 runtime、daemon 审计事件和隔离测试结果。

gVisor 官方安装说明：

<https://gvisor.dev/docs/user_guide/install/>

## 4. 构建 `tga-sandboxd`

开发或 CI 构建：

```bash
cd sandboxd
go mod download
go test -race ./...
go vet ./...
go build -trimpath -o tga-sandboxd ./cmd/tga-sandboxd
```

安装发布二进制：

```bash
sudo install -o root -g root -m 0755 \
  sandboxd/tga-sandboxd /usr/local/libexec/tga-sandboxd
```

正式发布应从 CI 的固定 Go 1.26.5 工具链生成二进制，并记录源码 commit、
Go module 校验和及构建产物摘要。运行主机不需要安装 Go。

## 5. 构建并固定 Kali 镜像

镜像构建必须使用摘要固定的 Kali 基础镜像。不要使用裸 `latest` 或可变 tag。

```bash
docker build \
  --build-arg KALI_BASE=kalilinux/kali-rolling@sha256:<BASE_DIGEST> \
  --target offline \
  --tag <REGISTRY>/tga-kali-offline:<RELEASE> \
  --file sandboxd/Dockerfile sandboxd

docker build \
  --build-arg KALI_BASE=kalilinux/kali-rolling@sha256:<BASE_DIGEST> \
  --target web \
  --tag <REGISTRY>/tga-kali-web:<RELEASE> \
  --file sandboxd/Dockerfile sandboxd

docker build \
  --build-arg KALI_BASE=kalilinux/kali-rolling@sha256:<BASE_DIGEST> \
  --target network \
  --tag <REGISTRY>/tga-kali-network:<RELEASE> \
  --file sandboxd/Dockerfile sandboxd
```

推送后只把 registry 返回的内容摘要写入运行配置：

```text
<REGISTRY>/tga-kali-network@sha256:<64位十六进制摘要>
```

发布流水线还应完成：

```bash
syft <IMAGE>@sha256:<DIGEST> -o spdx-json > image.spdx.json
trivy image --exit-code 1 <IMAGE>@sha256:<DIGEST>
cosign sign <IMAGE>@sha256:<DIGEST>
cosign verify <IMAGE>@sha256:<DIGEST>
```

`config/sandbox.json` 中的 `nmap` 和 `nmap-raw` 是 MCP 工具容器，不等同于
通用 Kali network 镜像。启用它们前必须另外提供一个实现 MCP STDIO 协议的
摘要固定镜像。没有该镜像时应保持对应 MCP server 为禁用状态。

## 6. 安装主机配置

创建目录：

```bash
sudo install -d -o root -g root -m 0755 /etc/tga
sudo install -d -o root -g root -m 0750 /var/lib/tga/runs
```

复制模板并保持禁用：

```bash
sudo install -o root -g root -m 0600 \
  config/sandbox.json /etc/tga/sandbox.json
```

编辑 `/etc/tga/sandbox.json`：

1. 保持 `"runtime": "disabled"`。
2. 把所有实际会启用的镜像替换为 `@sha256:<64 hex>`。
3. 确认 `sandboxd.socket_path` 为
   `/run/tga-sandboxd/sandboxd.sock`。
4. 确认 `sandboxd.run_root` 为 `/var/lib/tga/runs`。
5. 将 `sandboxd.allowed_client_uids` 设置为 Python API 服务账号的数字 UID；
   不要使用用户可控名称，也不要添加 Agent 或 Solver UID。
6. 只保留经审批的 Profile 和工具映射。
7. 检查各 Profile 的 CPU、内存、进程、时间和输出限制。
8. 不要把配置文件所有权交给运行 Python API 的账号。

如修改 `run_root`，必须同步修改 systemd unit 中的 `ReadWritePaths`。

先用 Python 严格解析器验证配置：

```bash
TGA_SANDBOX_CONFIG_PATH=/etc/tga/sandbox.json \
python -c "from tga.sandbox.config import load_sandbox_config; c,p=load_sandbox_config(); print(p, c.runtime, c.digest)"
```

再临时验证 enforced 约束。此命令只覆盖进程环境，不修改配置文件：

```bash
TGA_SANDBOX_CONFIG_PATH=/etc/tga/sandbox.json \
TGA_SANDBOX_RUNTIME=enforced \
python -c "from tga.sandbox.config import load_sandbox_config; c,p=load_sandbox_config(); print(p, c.runtime, c.digest)"
```

如果仍有占位摘要、重复键、未知 Profile 或未固定镜像，此步骤必须失败。

## 7. 安装 systemd 服务和 socket 权限

创建系统组：

```bash
sudo install -o root -g root -m 0644 \
  sandboxd/deploy/tga-sandboxd.sysusers \
  /usr/lib/sysusers.d/tga-sandboxd.conf
sudo systemd-sysusers
```

只把 Python API 服务账号加入 `tga-sandbox` 组：

```bash
sudo usermod -aG tga-sandbox <TGA_API_USER>
```

Agent、Solver、Web 用户、工具容器用户和普通开发账号不应加入该组。

安装并启动服务：

```bash
sudo install -o root -g root -m 0644 \
  sandboxd/deploy/tga-sandboxd.service \
  /etc/systemd/system/tga-sandboxd.service
sudo systemctl daemon-reload
sudo systemctl enable --now tga-sandboxd
```

检查：

```bash
sudo systemctl status tga-sandboxd
sudo journalctl -u tga-sandboxd --since "10 minutes ago"
sudo stat -c '%U %G %a %n' /run/tga-sandboxd/sandboxd.sock
```

socket 应为 `root tga-sandbox 660`。如果 Python API 是长期运行的 systemd
服务，加入组后需要重启该服务才能获得新的附加组。

## 8. MCP 配置迁移

先只预览：

```bash
python scripts/migrate_mcp_sandbox_v2.py config/mcp.json
```

确认变更后执行：

```bash
python scripts/migrate_mcp_sandbox_v2.py config/mcp.json --apply
```

脚本会保留 `.v1.bak`。迁移后逐项检查：

- 本地 STDIO MCP 必须具有 `executionProfileId`。
- `binwalk` 使用 `offline-analysis`。
- `nuclei`、`ffuf` 使用 `web-assessment`。
- Nmap connect 使用 `tcp-assessment`。
- 只有确实需要 SYN、Ping 或抓包的 Nmap 工具使用 `raw-network`。
- 高级网络 Task 必须同时在执行策略中填写规范化 `custom_cidrs`；TCP/SYN
  还必须填写 `custom_ports`。MCP 进程启动时会固化该快照，之后不能自行扩权。
- 未完成镜像和 Profile 审查的本地 MCP 保持禁用。
- 生产 enforced 模式不得使用 `local_process`。

## 9. 首次启用顺序

不要一次性启用全部能力。建议按以下顺序：

1. `runtime=disabled` 启动 Python API，完成数据库迁移。
2. 启动 `tga-sandboxd`，确认 Health、Docker、runsc、nftables 和 cgroup v2。
3. 只启用 `offline-analysis` 测试任务。
4. 验证 Task 和 Solver 工作区互相不可见。
5. 启用 `web-assessment`，验证默认拒绝和精确 HTTP/HTTPS allowlist。
6. 在隔离靶场启用 `tcp-assessment`。
7. 最后启用 `raw-network`，并验证只有该 Profile 具有 `NET_RAW`。
8. 检查终态 15 分钟后容器、网络、规则、进程和临时文件全部消失。
9. 完成上述检查后，将 `/etc/tga/sandbox.json` 的 runtime 改为
   `enforced`，重启 Python API 和 `tga-sandboxd`。

初期建议只对一个内部测试 Task 开启 enforced canary，而不是直接全量切换。

## 10. 必做验收

### 主机和服务

- [ ] `sbx diagnose` 无严重错误。
- [ ] `/dev/kvm` 对标准 Provider 可用。
- [ ] Docker Engine 健康。
- [ ] Docker 注册了 `runsc`。
- [ ] nftables 可执行语法检查和原子加载。
- [ ] 使用 cgroup v2。
- [ ] UDS 不监听 TCP，权限为 `0660`。
- [ ] 配置为 root 所有且不可由 API 用户修改。

### 隔离

- [ ] Task A 无法读取 Task B 的目录。
- [ ] 同 Task 的不同 Solver 默认无法读取对方目录。
- [ ] 沙箱内不存在宿主 Docker socket。
- [ ] 无法访问宿主 localhost、网桥网关、link-local 和云元数据地址。
- [ ] 未授权 CIDR、端口和私网连接被拒绝并记录审计事件。
- [ ] 普通 Profile 没有任何 capability。
- [ ] `raw-network` 只有 `NET_RAW`，没有 `NET_ADMIN` 或 `SYS_ADMIN`。
- [ ] `docker inspect` 确认高级网络容器实际使用 `runsc`。

### 生命周期

- [ ] Acquire、Destroy、StopProcess 和 Reconcile 重复调用保持幂等。
- [ ] 旧 fencing token 不能操作新实例。
- [ ] Exec 超时后不存在子进程。
- [ ] MCP STDIO 断开后不存在残留进程。
- [ ] daemon 重启后只协调带 TGA label 的资源。
- [ ] Task 终态 15 分钟后所有资源被回收。
- [ ] Provider 故障、版本不兼容和配置摘要变化全部失败关闭。

所有网络测试只能针对明确授权的本地靶场或保留测试网段执行。

## 11. 回滚

出现问题时先关闭控制面入口，不要删除审计数据库：

```bash
export TGA_SANDBOX_RUNTIME=disabled
sudo systemctl stop tga-sandboxd
```

随后：

1. 停止新的任务调度。
2. 保存 Python 审计记录和 daemon journal。
3. 列出带 `tga.sandbox.managed` label 的 Docker 资源。
4. 使用 TGA Destroy/Reconcile 清理，不执行无范围的 Docker 全局 prune。
5. 恢复 `/etc/tga/sandbox.json` 的上一份 root-owned 备份。
6. 修复并重新执行本手册的全部验收项。

## 12. 当前实现还需要改进的项目

### P0：正式启用前必须完成

1. **完成专用 Linux 集成测试。** 当前 Provider 已锁定 `sbx` 0.34.x 并使用
   `shell-docker` 外层 template 和受限内层工具容器；仍需在隔离 Runner
   验证真实 CLI、策略输出和镜像拉取行为。
2. **覆盖 runsc 和 nftables 真机边界。** 验证 runsc 身份、默认拒绝、实际
   Docker 网关拒绝、`NET_RAW`、MCP 双向流、超时和 daemon 重启。
3. **发布真实镜像。** 生成 SBOM、漏洞扫描、签名并替换全部占位摘要。
4. **启用 Nmap MCP 前发布其专用镜像。** 仓库已提供受 connect/raw 模式约束
   的实现，但运行配置仍必须引用签名摘要。

### P1：首个生产版本建议完成

1. 把 Task 外层沙箱身份与执行 Profile 解耦，支持一个 Task 在同一外层沙箱中
   安全运行多个 Solver/Profile 工具容器；当前 v1 对活动 Task 采用单一有效
   Profile。
2. 将 daemon 的实际状态和进程元数据持久化，进一步增强异常断电后的
   Reconcile；当前版本已使用 `SO_PEERCRED` 和 API UID allowlist。
4. 为 Docker Sandbox 长进程实现独立 stdout/stderr 并发读取、统一背压和线程
   清理。
5. 为镜像签名增加启动前强制验证，而不只验证摘要格式。
6. 增加磁盘配额、inode 配额、日志轮转和资源容量告警。
7. 增加 nftables 规则漂移检测，确认外部防火墙管理器不会覆盖 TGA 表。

### P2：后续能力

1. 可观测性：Prometheus 指标、OpenTelemetry trace、结构化安全事件。
2. 多台 sandboxd 主机调度和隔离池；v1 仍保持单机 UDS。
3. Masscan 专用限速、隔离网络和兼容性验证。
4. 配置签名、双人审批和自动化安全基线报告。

## 13. 常用文件

- `config/sandbox.json`：开发模板，默认 disabled。
- `config/mcp.json`：MCP v2 配置。
- `tga/sandbox/`：Python 控制面和 Provider。
- `sandboxd/api/sandbox/v1/sandbox.proto`：版本化协议。
- `sandboxd/cmd/tga-sandboxd/`：Go daemon 入口。
- `sandboxd/deploy/`：systemd 和 sysusers 文件。
- `sandboxd/Dockerfile`：固定 Kali 镜像构建定义。
- `docs/architecture/SANDBOX_RUNTIME.md`：架构边界说明。
- `.github/workflows/sandbox-runtime.yml`：Python、Go、race 和协议漂移检查。
