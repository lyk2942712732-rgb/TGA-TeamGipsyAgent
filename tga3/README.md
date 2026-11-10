# TGA3 运行时

部署、启动、配置、备份及项目目录统一见 [仓库 README](../README.md)，不再单独维护部署手册。

## 组件职责

- `app.py` / `config.py`：HTTP、WebSocket、MCP 接入与配置校验。
- `coordinator.py` / `aoe*.py`：任务状态、逐题认领、预算、增援与共同攻坚。
- `docker_runtime.py` / `workers/`：原生 CLI 生命周期、私有工作区、Hook、执行记录和上下文投递。
- `file_publications.py` / `submissions.py`：Finding、最终候选导入，证据关联与平台成功回执校验。
- `host_agents.py` / `experience.py`：Reporter、报告与任务内跨题经验卡。
- `storage.py` / `blackboard.py`：PostgreSQL 调度状态、逐题黑板及执行/证据索引。

AOE Scout 只收集题单提供的信息；普通单题 Scout 才执行侦察、提取、合并三阶段。Worker 的动作正文落入私有 `result/`，动作记录汇总在 `logs`，附件留在工作区。系统消费 Finding 和候选文件，Worker 无需直接维护数据库索引。

## 详细设计

| 说明 | 内容 |
|---|---|
| [AOE 数据流](docs/aoe-data-flow.md) | 题目数据库、JSON、黑板的使用边界 |
| [四 Worker 调度](docs/four-workers.md) | 换人、增援、共同攻坚与计时 |
| [靶机信息](docs/aoe-targets.md) | 实例接口、无靶机题与配额 |
| [平台提交](docs/platform-submissions.md) | flag、平台回执、失败补发与截止处理 |
| [文件发布](FILE-PUBLICATIONS.md) | Finding 与 final-candidate 的文件导入 |
| [经验卡](EXPERIENCE.md) | 成功题目局部经验的提炼与同任务读取 |
| [内部状态](RUNTIME-STATE.md) | `.tga3`、导入回执与恢复资料 |
| [工具范围](WORKER-TOOLS.md) | 原生 CLI 与项目工具的分工 |
| [镜像环境](docker/TOOL-BASELINE.md) | 单一 Python 环境及工具兼容依赖 |

历史设计文档保留作审查候选；如与当前实现不同，以代码和根 README 为准。
