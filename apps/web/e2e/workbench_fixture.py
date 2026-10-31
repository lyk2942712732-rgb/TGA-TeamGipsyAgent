"""Real API, isolated storage and deterministic sample data for design verification."""
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from uuid import UUID

import uvicorn

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / "tga3/src"))
from tga3.app import create_app
from tga3.config import ModelsConfig, TGA3Config
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import Actor, AgentRun, AgentState, Artifact, ArtifactRef, EventKind, PublishRequest, RunState
from tga3.host_agents import DeterministicHostModel
from tga3.storage import InMemoryStorage

TASK_ID = UUID("00000000-0000-4000-8000-000000001042")
REPORT_ID = UUID("00000000-0000-4000-8000-000000001038")

with TemporaryDirectory(prefix="tga-workbench-") as directory:
    fixture = Path(directory)
    copytree(root / "tga3/config", fixture / "config")
    config = TGA3Config(fixture / "config")
    for name in ("workspace_root", "input_root", "artifact_root", "writeup_root", "skills_root"):
        setattr(config.runtime, name, str(fixture / name))
    endpoint = {"base_url": "http://127.0.0.1:18084/mock/v1", "models": [{"id": "deepseek-v4.1-flash", "name": "deepseek-v4.1-flash", "enabled": True}, {"id": "qwen3-coder-plus", "name": "qwen3-coder-plus", "enabled": True}]}
    config.models = ModelsConfig.model_validate({"schema_version": 1, "providers": [{"id": "workbench", "name": "联调示例网关", "endpoints": {"openai_responses": endpoint, "anthropic": endpoint}, "api_keys": [{"id": "primary", "label": "本地测试", "api_key": "fixture-only-key"}], "selected_api_key_id": "primary"}]})
    for agent in config.agents.agents.values():
        agent.provider_id = "workbench"
        agent.model_id = "deepseek-v4.1-flash"
    db = InMemoryStorage()
    app = create_app(config, db, FakeContainerRuntime(), host_model=DeterministicHostModel())

    async def seed():
        await db.create_task("旧城登录页（联调示例）", "penetration_test", TASK_ID)
        await db.update_task(TASK_ID, state=RunState.RUNNING)
        for agent_id, definition in config.agents.agents.items():
            state = AgentState.COMPLETED if agent_id == "scout" else AgentState.CREATED if agent_id == "reporter" else AgentState.RUNNING
            await db.upsert_agent(AgentRun(task_id=TASK_ID, agent_id=agent_id, sdk=definition.runtime, provider_id="workbench", model_id=definition.model_id, protocol=definition.protocol, actual_state=state, desired_state=state, metadata={"blackboard_cursor": 3, "blackboard_read_at": time.time()} if definition.role == "worker" else {}))
        user = Actor(agent_id="user", display_name="用户", role="user")
        for agent_id, inputs, outputs in [("scout", 1000, 200), ("worker-openai", 2000, 400)]:
            journal = Path(config.runtime.workspace_root) / str(TASK_ID) / agent_id / ".codex/sessions/usage.jsonl"
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": inputs, "output_tokens": outputs},
            }}}) + "\n")
        journal = Path(config.runtime.workspace_root) / str(TASK_ID) / "worker-claude/.claude/projects/-workspace/usage.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(json.dumps({"type": "assistant", "message": {"id": "fixture-1", "usage": {
            "input_tokens": 500, "cache_read_input_tokens": 400, "cache_creation_input_tokens": 100,
            "output_tokens": 300,
        }}}) + "\n")
        scout = Actor(agent_id="scout", display_name="Scout", role="scout")
        codex = Actor(agent_id="worker-openai", display_name="OpenAI Worker", role="worker")
        claude = Actor(agent_id="worker-claude", display_name="Claude Worker", role="worker")
        await db.publish(TASK_ID, user, PublishRequest(kind="user_prompt", body={"text": "检查附件中的前端资源，验证题目的可用入口并记录证据。目标为本地 CTF 示例。"}, idempotency_key="prompt"))
        await db.publish(TASK_ID, scout, PublishRequest(kind="intel", body={"claim": "发现登录入口与账户 API", "detail": "前端资源包含 /login、/api/session 与 /api/accounts 请求格式，可作为后续验证入口。"}, idempotency_key="intel"))
        artifact_path = fixture / "request.txt"
        artifact_path.write_text("GET /api/accounts/1024\nHTTP/1.1 200 OK\n[fixture evidence]", encoding="utf-8")
        artifact = await db.register_artifact(Artifact(task_id=TASK_ID, created_by=claude, name="account-check.txt", storage_path=str(artifact_path), media_type="text/plain", sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(), size_bytes=artifact_path.stat().st_size))
        await db.publish(TASK_ID, claude, PublishRequest(kind="finding", body={"claim": "账户接口存在可复现的越权读取", "detail": "使用同一身份对比账户标识变化前后的响应，已保存请求、响应和最小复现材料。"}, artifact_refs=[ArtifactRef(artifact_id=artifact.id, locator="request.txt:1-3", description="对照请求和响应")], idempotency_key="finding"))
        await db.append_event(TASK_ID, codex, EventKind.ACTION_STARTED, "python check_session.py", channel_agent_id="worker-openai", payload={"tool": "shell", "tool_use_id": "shell-1", "command": "python /workspace/check_session.py"})
        await db.append_event(TASK_ID, claude, EventKind.ACTION_STARTED, "读取请求验证说明", channel_agent_id="worker-claude", payload={"tool": "Read", "tool_use_id": "read-1", "input": {"file_path": "/skills/ctf-web/requests.md"}})
        await db.create_task("镜中密文（联调示例）", "cryptography", REPORT_ID)
        await db.publish(REPORT_ID, user, PublishRequest(kind="user_prompt", body={"text": "恢复被隐藏的明文。"}, idempotency_key="report-input"))
        report_path = fixture / "writeup.md"
        report_path.write_text("# 镜中密文：解题报告\n\n## 解题结论\n\n附件中的密文经过编码与重排，逐步还原后获得验证结果。\n\n## 关键步骤\n\n1. 确认输入格式与字符集。\n2. 对比编码层和重排规则。\n3. 使用脚本复现解码过程。\n\n```python\nprint('fixture result')\n```\n\n| 证据 | 结果 |\n| --- | --- |\n| 编码检查 | 可复现 |\n| 结果核验 | 已完成 |\n\n这是隔离联调环境中的示例内容。\n", encoding="utf-8")
        await db.save_writeup(REPORT_ID, 1, str(report_path), hashlib.sha256(report_path.read_bytes()).hexdigest())
        await db.update_task(REPORT_ID, state=RunState.COMPLETED)
        paused = await db.create_task("堆上的回声（联调示例）", "pwn")
        await db.update_task(paused.id, state=RunState.RUNNING)
        await db.upsert_agent(AgentRun(task_id=paused.id, agent_id="worker-claude", sdk="claude_cli", provider_id="workbench", model_id="deepseek-v4.1-flash", protocol="anthropic", desired_state=AgentState.PAUSED, actual_state=AgentState.PAUSED))
        for package in ("ctf-web", "ctf-common"):
            path = config.skills_root / package
            path.mkdir(parents=True, exist_ok=True)
            (path / "SKILL.md").write_text(f"# {package}\n\n消费 Scout 的 Intel，选择具体路径验证。\n\n## 产出\n\n只发布经过验证的 Finding，记录复现步骤与证据位置。\n", encoding="utf-8")
            (path / "requests.md").write_text("# 请求与响应\n\n保存最小请求、认证状态和关键响应。\n", encoding="utf-8")

    asyncio.run(seed())

    @app.get("/mock/v1/models")
    async def mock_models():
        return {"data": [{"id": "deepseek-v4.1-flash"}, {"id": "qwen3-coder-plus"}]}

    @app.post("/fixture/activity")
    async def fixture_activity():
        """Exercise real event delivery in the isolated verification app only."""
        for agent_id, tool, tool_input in [
            ("worker-openai", "mcp__blackboard__blackboard_sync", {"after_seq": 2}),
            ("worker-claude", "Read", {"file_path": "/skills/ctf-web/requests.md"}),
        ]:
            actor = Actor(agent_id=agent_id, display_name=config.agents.agents[agent_id].display_name, role="worker")
            await db.append_event(TASK_ID, actor, EventKind.ACTION_STARTED, tool, channel_agent_id=agent_id, payload={"tool": tool, "input": tool_input, "tool_use_id": f"visual-{time.time_ns()}"})
        return {"ok": True}

    @app.post("/fixture/scout-phase/{phase}")
    async def fixture_scout_phase(phase: str):
        assert phase in {"executing", "extracting"}
        await db.update_task(TASK_ID, state=RunState.SCOUTING)
        await db.update_agent(TASK_ID, "scout", actual_state=AgentState.RUNNING,
                              metadata={"scout_phase": phase})
        return {"ok": True}

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TGA_WORKBENCH_API_PORT", "18084")))
