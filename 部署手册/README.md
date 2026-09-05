# TGA3 部署手册

本目录位于 `TGA-TeamGipsyAgent` 仓库根目录，是项目唯一的完整部署逻辑所在位置。当前版本采用“宿主机控制面 + PostgreSQL 容器 + 按任务动态 Worker 容器 + Nginx 静态前端”的架构，完整部署支持通用 Linux。

## 快速选择

| 场景 | 推荐方式 | 入口 |
|---|---|---|
| Linux 首次部署或演示 | 统一部署脚本 | `./deploy.sh` |
| Windows 或 macOS 环境检查 | 只执行预检 | `deploy.bat --check-only` 或 `python3 deploy.py --check-only` |
| 需要逐步确认每个环节 | 手动部署 | [详细手动部署指南](详细手动部署指南.md) |
| 长期运行并通过域名访问 | systemd + Nginx | [生产环境部署](生产环境部署.md) |
| 部署后验收或排错 | 自动验证脚本 | `python 部署验证脚本.py` |

## 架构摘要

```text
浏览器
  │ HTTP/S
  ▼
Nginx :80/:443
  ├── /              -> apps/web/dist
  └── /api/v3/*      -> TGA3 控制面 :8083
                              ├── PostgreSQL :5433（仅本机）
                              ├── /mcp
                              └── /internal/agents（WebSocket）
                                      ▲
                         按任务动态创建两个 Worker 容器
                         OpenAI Worker / Claude Worker
```

Worker 不需要手工启动，也不应写进 Compose 常驻服务。创建任务时，控制面通过 Docker SDK 创建 Worker；任务结束或停止时回收容器。

## 环境要求

生产部署可使用满足依赖要求的主流 Linux 发行版，例如 Debian、Ubuntu、Rocky Linux、AlmaLinux、RHEL 或同类系统。准备以下软件：

- Python 3.11 或更高版本，且能够创建 `venv`。
- Docker Engine 和 Docker Compose v2；`docker compose up --help` 中应包含 `--wait`。
- Node.js 18 或更高版本及 npm。
- Git、curl 和 Nginx；Nginx 仅在生产反向代理时需要。
- 可访问模型供应商 API 和 Kali 软件源的网络。

资源按默认配置估算：每个 Worker 上限为 2 CPU、4096 MB 内存，两个 Worker 可并行运行。建议至少 8 核 CPU、16 GB 内存和 50 GB 可用磁盘；构建 Kali 基础镜像时应预留更多磁盘和时间。

## 快速部署

从源码仓库根目录执行，目录结构如下：

```text
TGA-TeamGipsyAgent/
├── apps/
├── tga3/
└── 部署手册/              # 唯一部署入口
```

Linux 执行：

```bash
cd 部署手册
chmod +x deploy.sh
./deploy.sh
```

`deploy.sh` 只进入同目录的 `deploy.py`，所有完整部署步骤只在 `deploy.py` 维护。脚本会统一执行环境检查、启动 PostgreSQL、调用源码镜像构建组件、创建 Python 虚拟环境、安装控制面，并构建前端。首次构建耗时主要取决于 Kali 镜像下载与软件包安装。

构建完成后，在前台启动控制面：

```bash
cd ../tga3
.venv/bin/tga3 --config-dir config serve
```

另开终端，从源码仓库根目录启动前端开发服务器：

```bash
cd apps/web
npm run dev
```

访问 `http://127.0.0.1:5173`。控制面健康检查为 `http://127.0.0.1:8083/api/v3/health`，直接访问 API 文档可使用 `http://127.0.0.1:8083/docs`。

进入前端“配置中心”后，依次保存供应商地址和 API 密钥、模型、Agent 绑定与运行参数。密钥未配置时控制面可以启动，但新任务无法正常调用模型。

## 文件说明

| 文件 | 用途 |
|---|---|
| `总体部署文档.md` | 架构、端口、部署路径和验收标准总览 |
| `详细手动部署指南.md` | 从系统准备到启动服务的逐步命令 |
| `docker-compose-部署.md` | 说明 Compose 只管理 PostgreSQL，以及 Worker 的容器生命周期 |
| `容器化部署指南.md` | Worker 镜像分层、挂载、网络、资源与安全边界 |
| `生产环境部署.md` | systemd、Nginx、权限、备份、更新和安全加固 |
| `配置文件说明.md` | 四个 JSON 配置文件的字段、约束与修改规则 |
| `故障排除指南.md` | 按症状定位数据库、API、前端、镜像和 Worker 问题 |
| `deploy.py` | 唯一完整部署编排，支持通用 Linux；其他平台只支持预检 |
| `deploy.sh` | Linux 统一入口，只转交给 `deploy.py` |
| `deploy.bat` | Windows 预检入口，不执行生产部署 |
| `部署验证脚本.py` | 只读检查文件、配置、Docker、镜像、数据库和 HTTP 服务 |

## 端口和访问地址

| 组件 | 默认地址 | 暴露建议 |
|---|---|---|
| Nginx 前端 | `http://服务器地址/` | 生产公开 80/443 |
| TGA3 API | `http://127.0.0.1:8083/api/v3` | 通过 Nginx 暴露；8083 不对公网开放 |
| API 文档 | `http://127.0.0.1:8083/docs` | 仅运维访问 |
| Vite 开发服务 | `http://127.0.0.1:5173` | 仅开发环境 |
| Vite 预览服务 | `http://127.0.0.1:4173` | 仅本机验收 |
| PostgreSQL | `127.0.0.1:5433` | 仅本机 |

`/mcp` 和 `/internal/agents` 是 Worker 与控制面通信端点。生产 Nginx 示例不向外代理它们；Worker 通过 `host.docker.internal:8083` 访问宿主机。

## 部署验证

开发模式：

```bash
python 部署验证脚本.py \
  --frontend-url http://127.0.0.1:5173
```

生产模式：

```bash
python3 部署验证脚本.py \
  --frontend-url http://127.0.0.1 \
  --require-api-key
```

仅在服务启动前检查环境和构建产物：

```bash
python3 部署验证脚本.py --preflight
```

验证通过至少应满足：PostgreSQL 健康、两个 Worker 镜像存在、`/api/v3/health` 返回 `status=ok`、前端可访问，并且实际使用的 Provider 已配置非空 API 密钥。

## 重要限制

- 当前数据库没有迁移机制。开发或测试可按文档重建数据卷；生产环境发现 `schema.sql` 变化时，必须先制定迁移方案，不能直接执行 `docker compose down -v`。
- Provider 密钥保存在 `tga3/config/models.json` 中，不使用环境变量覆盖。请将配置目录权限限制给服务账户，并从代码提交、日志和普通备份中排除密钥。
- 默认 Worker 需要 `NET_RAW` 和 `SYS_PTRACE`，但会丢弃其他 capability 并启用 `no-new-privileges`。只应处理授权目标和任务。
- 项目未提供 Kubernetes 清单。当前控制面依赖宿主 Docker daemon 动态创建 Worker，不应直接照搬旧项目的 Kubernetes 部署章节。
- 源码中的 `tga3/scripts/ubuntu-bootstrap.sh` 仅为旧命令兼容入口，会转交给本目录的统一部署脚本，不再维护另一套步骤。

## 文档版本

- 手册版本：1.0
- 对应项目版本：TGA3 0.1.0
- 更新日期：2026-09-05
