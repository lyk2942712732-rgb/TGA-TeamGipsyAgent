# TGA · Team Gipsy Agent

TGA 是面向 CTF 的多智能体任务系统。Scout 收集题目，Codex、Claude Code、OpenCode、Qwen Code 四个 Worker 并行求解，通过逐题黑板共享事实；Reporter 根据执行证据生成报告，并提炼本次任务可复用的经验卡。

本分支 `codex/scout-two-stage` 是日常主版本：Linux 宿主机运行控制服务和 PostgreSQL，Agent 在动态创建的独立执行容器中工作。受限评测使用独立的 [托管分支](https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent/tree/codex/tsecbench-hosted)，部署方式见该分支 README。

## 当前工作方式

- **AOE 多题**：Scout 只读取任务提供的题单接口及其明确给出的附件入口，不猜接口、不启动靶场。题目登记后即可并行认领，无需等待全批采集结束。任务总览保存共享场景、初始要求、接口和凭证；题目黑板保存单题信息和进展。
- **认领与预算**：四个 Worker 按未经尝试、难度和靶机配额等规则分题；支持换人尝试、增援和最后共同攻坚。首次、第二次、攻坚预算分别配置，从首条实际 action 开始计时。
- **普通单题**：Scout 的侦察、逐 action 提取 Intel、合并 Intel 是同一容器和 CLI 会话中的三种工作内容。第二、三阶段以完整覆盖为结束条件。
- **执行与共享**：CLI 管理原生工具和上下文；系统记录动作、保存原始输出、关联附件。Worker 写 Finding 文件，系统校验证据并导入黑板。最终候选必须通过 flag 与平台成功回执校验。
- **经验卡**：成功题目中的局部解决经验由 Reporter 提炼；同一 AOE 任务的其他题可读取目录并按需阅读正文和脚本，不跨任务复用。
- **外部能力**：主版本保留 ctf42 MCP 和按配置开放的网页搜索；Skill 通过 `/skills` 按需读取，正文不逐轮重复注入。现有九类专项包的来源及范围见 [Skill 目录](tga3/config/skills/README.md)。

## 项目文件

| 路径 | 用途 |
|---|---|
| `apps/web/` | React / Vite 前端，任务、黑板、运行图和配置中心 |
| `tga3/src/tga3/` | 控制服务、AOE 调度、原生 CLI 适配、Hook、文件导入 |
| `tga3/config/` | 模型、Agent、场景、运行配置与 Skills |
| `tga3/docker/` | slim 工具基础环境与四种 CLI 镜像构建文件 |
| `tga3/scripts/` | 构建、部署和只读验证脚本 |
| `tga3/deploy/` | systemd 与 Nginx 配置样例 |
| `tga3/schema.sql`、`compose.yaml` | 数据库结构、开发/单机 PostgreSQL 容器 |
| `tga3/data/` | 本地运行数据，不提交 Git |
| `tga3/tests/`、`apps/web/e2e/` | 后端、前端与集成验证 |

## Linux 部署

需要 Python 3.11+、Docker Engine、Compose v2、Node.js 22/npm、Git；使用静态前端时另需 Nginx。执行容器使用 Python 3.12 slim-bookworm、同一套 Python 工具环境和固定版本 CLI；不依赖旧 Kali/GHCR 镜像。工具清单与依赖分工见 [工具环境](tga3/docker/TOOL-BASELINE.md)。

四个 Worker 默认各有 2 CPU / 4 GiB 上限，这些是限额而非常驻预分配。实际资源随编译、扫描等工作变化；为并行求解及镜像构建预留内存和磁盘。

### 构建与启动

```bash
git clone --branch codex/scout-two-stage https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent.git
cd TGA-TeamGipsyAgent
python3 tga3/scripts/deploy.py
```

部署脚本检查依赖、启动 PostgreSQL、构建共享基础镜像与 Codex/Claude Worker、安装宿主机控制服务并构建前端。目前额外两种 Worker 的构建命令如下，首次启用前执行：

```bash
cd tga3
for worker in opencode qwen; do
  docker build --network=host --build-arg CLI_BASE_IMAGE=tga3-cli-base:local     -f docker/Dockerfile.$worker -t tga3-worker-$worker:local .
done
.venv/bin/tga3 --config-dir config serve
```

也可分步部署，等价命令为：

```bash
cd tga3
docker compose up -d --wait postgres
bash scripts/build-images.sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[control]'
cd ../apps/web
npm ci
npm run build
```

随后执行上面的 OpenCode/Qwen 构建与控制服务启动命令。宿主机 `.venv` 服务于控制面，与容器内统一 Python 环境不同。完整部署脚本的 `--skip-images` 只跳过构建，不保证所需镜像已存在。

开发时另开终端 `cd apps/web && npm run dev`，访问 `http://127.0.0.1:5173`；控制面默认 `:8083`，健康检查为 `/api/v3/health`。进入配置中心依次配置模型供应商、密钥、各 CLI 的实际协议与模型、运行参数；创建任务选择需要的场景，AOE 必须明确选中。

### 长期运行

- 使用 [systemd 样例](tga3/deploy/tga3.service) 和 [Nginx 样例](tga3/deploy/nginx-tga3.conf)，按实际仓库位置修改 `/opt/TGA-TeamGipsyAgent` 与运行账户。运行账户需要 Docker 权限和配置、data 的写权限。
- Agent 挂载目录需要与镜像 UID/GID 的读写权限一致；不要通过整体改为 root 来掩盖挂载错误。Nginx 需要读取静态文件及其所有父目录，通常目录 `0755`、静态文件 `0644`。
- Nginx 提供 `apps/web/dist`，将 `/api/v3/` 代理到 `127.0.0.1:8083`；容器另通过 runtime 中的 WebSocket/MCP 地址连接控制服务。这些内部地址必须从容器可达。
- PostgreSQL 通过 Compose 只绑定 `127.0.0.1:5433`。`compose.yaml` 中数据库凭证为单机默认值，正式部署修改时同时更新 `runtime.json` 的 DSN。不要把管理界面或带凭证的配置暴露到公网。
- 按需启用服务：修改并安装样例后执行 `sudo systemctl daemon-reload`、`sudo systemctl enable --now tga3`；Nginx 修改后先 `sudo nginx -t` 再 reload。

更新前保留配置和数据备份；有运行任务时不要直接重启服务或重建其执行环境。更新后端需要重新安装控制包并重启，更新前端需要重新构建；Worker 包在镜像内，修改其运行代码后需要重建相应镜像。不要用 `docker compose down -v` 更新服务。

数据库备份示例：

```bash
docker exec tga3-postgres pg_dump -U tga3 tga3 > tga3-backup.sql
```

同时保存 `tga3/config/` 和 `tga3/data/`。配置可能含密钥，备份不能直接上传公开仓库。已有卷不重复执行初始化 schema，升级行为应结合实际代码迁移检查。

只读预检/验收入口：`python3 tga3/scripts/verify-deployment.py --source-dir .`。服务排障先看 `journalctl -u tga3`、数据库健康状态、容器日志以及挂载权限，不重复创建任务试错。

## 配置与数据

`models.json` 保存供应商端点、模型和密钥；`agents.json` 保存角色、CLI 与提示词；`scenes.json` 保存场景；`runtime.json` 保存目录、资源和通信配置。新任务读取保存后的配置，已有任务保留自己的运行状态。

PostgreSQL 保存任务/题目调度状态、黑板和索引；文件保存原始执行资料及可查看的 JSON。主要结构：

```text
tga3/data/
├── tasks/<任务ID>/
│   ├── inputs/                    用户上传材料，仅 Scout 读取
│   ├── questions.json             AOE 题单
│   └── <题目ID>/                  AOE 单题；普通任务的同类目录直接在任务根部
│       ├── question.json
│       ├── inputs/                题单明确提供的题目附件
│       ├── workspaces/<agent>/result/
│       ├── logs/<agent>/<agent>-actions.json
│       ├── findings/<agent>/
│       ├── intels/scout/{raw,merged}/
│       ├── final-candidate.json
│       └── writeups/
└── experience/{cards,assets}/     物理存储；按所属任务过滤可见范围
```

AOE 前置收集不生成普通三轮 Intel；不要把目录存在理解为每种场景都会产生对应文件。证据与会话恢复资料属于运行数据，不是无用缓存。

## 开发与详细说明

```bash
cd tga3
python -m pip install -e '.[dev,control]'
pytest
cd ../apps/web
npm ci
npm test
npm run build
```

[运行时说明](tga3/README.md) · [AOE 数据流](tga3/docs/aoe-data-flow.md) · [四 Worker 调度](tga3/docs/four-workers.md) · [文件发布](tga3/FILE-PUBLICATIONS.md) · [经验卡](tga3/EXPERIENCE.md)。

本项目用于获得授权的 CTF、靶场与安全评测。许可证见 [LICENSE](LICENSE)。

待用户决定的旧文件与临时产物见 [文件整理清单](tga3/docs/file-cleanup-review.md)，清单所列候选尚未删除。
