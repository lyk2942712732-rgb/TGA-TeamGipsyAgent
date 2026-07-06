# 🏗️ 角色 4：测试与基础设施工程师 (Infra & Testing Engineer)

## 🎯 核心目标
你的任务是为团队提供稳固的后勤保障。你需要确保 Agent 代码能够被正确打包（符合平台要求的 Docker 格式），并且团队其他成员能在本地不依赖真实平台也能顺畅开发和测试。

## 📁 负责目录
项目根目录（`Dockerfile`, `pyproject.toml`）以及 `my-ctf-agent/tests/`

## 🏗️ 架构设计与实现指导

### 1. 容器化打包 (Dockerfile)
* **背景**：CTF 平台（由 `ctf-agent-benchmark` 驱动）通常要求参赛者提交一个 Docker 镜像，平台会调度这个镜像来打比赛。
* **设计指导**：
  * 编写轻量、安全的 `Dockerfile`。推荐使用 Python 3.11+ 的 slim 镜像。
  * 确保所有系统级依赖（比如 Agent 需要的 `nmap`, `curl`, 或专门的工具链）都在 Dockerfile 中安装。
  * 使用 `uv` 或标准的 `requirements.txt` 管理依赖，保证环境一致性。

### 2. 本地模拟环境 (Mock Server)
* **痛点**：如果在开发期间频繁调用平台的真实靶机，一方面速度慢，另一方面可能受制于平台的频率限制。
* **设计指导**：
  * 在 `tests/mock_mcp_server/` 下搭建一个极简的 FastAPI 服务，模拟官方平台的 MCP 接口（能吐出假题目、返回假的启动成功信息）。
  * 搭建一个或者几个本地的简单 Vulnerable App（比如一个带有简单 SQL 注入的 PHP 容器），写好 `docker-compose.yml`，供团队成员在本地一键启动并让 Agent 攻击。

### 3. 可观测性与日志 (Logging & Tracing)
* **痛点**：Agent 是个黑盒，当它找不到 Flag 时，很难知道它到底卡在了哪一步。
* **设计指导**：
  * 接入标准日志（Logging），将 Agent 的每一步输出格式化打印到控制台。
  * **强烈建议**：接入 LangSmith 或者在本地写一个日志收集器，把大模型每一次的 Prompt 和 Response 完整记录下来，方便复盘大模型是怎么“死”的。

## 🤝 协作指南
* 把控依赖库，如果有成员要求引入新的 Python 包，由你统一更新 `pyproject.toml` 或 `requirements.txt`。
* 帮助 **角色 2** 在本地测试 MCP 客户端的连接稳定性。
