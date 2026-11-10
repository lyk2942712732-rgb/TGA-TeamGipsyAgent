# 文件整理待审清单

本清单中的文件**尚未删除**，由用户按编号决定。检查日期：2026-11-10。
范围为普通分支 `codex/scout-two-stage` 与托管分支 `codex/tsecbench-hosted`；本地未跟踪文件另列。

## 已按授权完成

- 两个分支删除根部 `部署手册/`；仍有用的 Python 部署与只读验证程序移至 `tga3/scripts/deploy.py`、`verify-deployment.py`，旧 bootstrap 改为转交新入口。
- 根 README 分别说明普通 Linux 与托管部署；`tga3/README.md` 改为运行时组件与资料导航。
- 托管分支删除独立 `tga3/HOSTED.md`，其部署、变量、镜像下载与验收说明已并入根 README。
- GitHub 删除旧 v0.1.0/v0.1.1 两个 Release、38 条 sandbox-integration 部署记录、24 个旧 Kali 容器包。
- 保留新托管 Release `hosted-20261110` 和 `.tar` 镜像附件；本次不删旧 Git tag、不改默认分支、不删除任务数据。

## 可选删除项

| 编号 | 文件或目录 | 分支/位置 | 作用与当前判断 | 删除影响 |
|---|---|---|---|---|
| C01 | `tga3/CLI-MIGRATION.md` | 两分支 | 历史 CLI/SDK、认证与目录迁移说明；多次变更追加，部分内容已过时 | 运行无影响，失去迁移背景；需要同步移除其他文档里的旧链接 |
| C02 | `tga3/CODE-VS-MODEL-REVIEW.md` | 两分支 | 先前对代码/模型分工的审查与改进建议，不是运行配置 | 运行无影响，失去历史讨论记录 |
| C03 | `tga3/FINDING-PROMPTS.md` | 两分支 | 手工复制 Worker 提示词；真实提示词来自 `agents.json` 和运行代码，容易不同步 | 运行无影响；删除前可决定是否需要独立提示词参考 |
| C04 | `tga3/docs/knowledge-ctf42.md` | 仅托管分支 | 记录在线知识库接入，托管版已移除该功能 | 托管运行无影响；普通分支仍有实际作用，应保留 |
| C05 | `apps/web/` | 仅托管分支 | 普通控制台源码与前端测试；托管镜像不打包、不启动 Web UI | 不影响已发布镜像；分支前端开发/联调能力丢失，普通部署脚本和验证器需跟着调整 |
| C06 | `tga3/compose.yaml`、`tga3/deploy/`、普通 `scripts/deploy.py`、`verify-deployment.py`、`ubuntu-bootstrap.sh` | 仅托管分支 | 普通宿主机部署入口/样例；托管 `hosted.py` 自启私有数据库和控制服务 | 托管入口不使用它们；删除会失去该分支的普通部署路径，需要更新 README |
| C07 | `.codex-video-review/` | 主工作区，未跟踪，约 16.5 MB | 过去前端录像/截图检查的临时产物 | 不影响运行；失去对应视觉证据 |
| C08 | `tmp/` 中旧补丁、构建上下文、审计脚本和临时工作区 | 主工作区，未跟踪，总计约 3.69 GB | 历次调查、部署、验证与下载工具，不参与项目打包；其中部分保留连接配置 | 应分组删，不能整目录盲删：`hosted-release/` 有当前 1.19 GB 镜像，`task-*-audit/monitor/` 有用户要求保留的分析证据 |
| C09 | `apps/web/node_modules/` | 主工作区，未跟踪，约 201 MB | 本地前端依赖，可从 package-lock.json 重装 | 删除后先 `npm ci` 才能构建/测试；不直接清理线上目录 |
| C10 | `apps/web/dist/` | 主工作区，构建产物，约 0.66 MB | 本地生产前端输出，可重新生成 | 本地删除后需重新构建；Ubuntu 正在提供服务的 dist 必须保留 |
| C11 | `.pytest_cache/`、`.ruff_cache/`、`**/__pycache__/` | 本地生成物 | 测试、检查与 Python 字节码缓存；根 `tests/` 只剩 3 个旧 TGA2 字节码文件，约 0.25 MB | 可重建，不是测试源码；勿误删 `tga3/tests` 与前端测试 |
| C12 | `apps/web/pnpm-lock.yaml`、`pnpm-workspace.yaml` | 主工作区，未跟踪 | 另一包管理器的配置；当前提交、部署与 CI 命令使用 npm/package-lock.json | 当前 npm 流程不依赖它们；如另有 pnpm 工作流，删除会影响其锁版本 |
| C13 | `tga3/src/tga3/task_storage.py`、`intel_migration.py` 及其专用迁移测试 | 两分支，条件候选 | 旧目录结构、旧 Intel 格式的离线迁移工具；不在正常求解热路径执行 | 只有确认不再需要读取/迁移旧数据后才建议删除；历史任务仍在时优先保留 |

## 不应按“没用”直接删除

- `tga3/src/tga3/docker_runtime.py`：托管进程运行时仍复用其中的类型和启动构建逻辑，不是只因托管不运行 Docker 就可删除。
- `.tga3`、动作 logs、原始 result、发布回执、原生 CLI 会话：属于恢复、证据关联和审计资料。
- `EXPERIENCE.md`、`FILE-PUBLICATIONS.md`、`RUNTIME-STATE.md`、`WORKER-TOOLS.md` 及当前 AOE 设计文档：仍描述实际功能；个别旧表述可修订，不等于文件整体失效。
- 新托管 `.tar`、环境变量模板、Dockerfile、OpenCode 插件锁文件、基础工具依赖约束：用于交付和复现。
- PostgreSQL 卷、`data/`、模型配置：本次仅检查文件用途，不删除业务数据、密钥或已安装工具环境。

候选体积为本地检查值，不等于 Git 仓库大小，也不等于托管镜像大小。主工作区 tmp 与 node_modules 等没有推到 GitHub，删除不能减少远程代码仓库的体积。
