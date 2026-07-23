from __future__ import annotations

import json
import sys
from pathlib import Path

from tga.contracts import TGATask
from tga.evidence.store import EvidenceStore
from tga.runtime.manager import Manager
from tga.tools.mcp_manager import MCPCallOutcome, MCPManager


class NeverExecutor:
    def execute(self, **_):
        raise AssertionError("dynamic MCP calls must bypass the legacy generic executor")


class MCPFlagModel:
    model = "mcp-agent-test"

    def __init__(self) -> None:
        self.tools = []
        self.turn = 0

    def chat_tools(self, messages, *, tools, temperature=0.2):
        self.tools = tools
        self.turn += 1
        if self.turn > 1:
            result = json.loads(next(item["content"] for item in reversed(messages) if item["role"] == "tool"))
            return {"message": {"role": "assistant", "content": "", "tool_calls": [{
                "id": "finish_mcp", "type": "function", "function": {
                    "name": "finish_session", "arguments": json.dumps({
                        "summary": "The MCP result contains the verified flag.",
                        "evidence_artifact_ids": result["artifact_ids"],
                        "flag": "CTF{dynamic_mcp}",
                    }),
                },
            }]}, "finish_reason": "tool_calls"}
        return {
            "message": {
                "role": "assistant",
                "content": "Calling the discovered MCP tool.",
                "tool_calls": [
                    {
                        "id": "mcp_call_1",
                        "type": "function",
                        "function": {
                            "name": "mcp__fixture__echo",
                            "arguments": json.dumps({"text": "CTF{dynamic_mcp}", "token": "supersecret"}),
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }


class MCPGatewayFlagModel:
    model = "mcp-gateway-test"
    def __init__(self) -> None: self.tools = []; self.turn = 0
    def chat_tools(self, messages, *, tools, temperature=0.2):
        self.tools = tools; self.turn += 1
        if self.turn > 1:
            result = json.loads(next(item["content"] for item in reversed(messages) if item["role"] == "tool"))
            return {"message": {"role": "assistant", "content": "", "tool_calls": [{
                "id": "finish_gateway", "type": "function", "function": {
                    "name": "finish_session", "arguments": json.dumps({
                        "summary": "The MCP gateway result contains the verified flag.",
                        "evidence_artifact_ids": result["artifact_ids"], "flag": "CTF{gateway_mcp}",
                    }),
                },
            }]}, "finish_reason": "tool_calls"}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "gateway_call_1", "type": "function", "function": {
                "name": "tga_mcp",
                "arguments": json.dumps({"action": "call", "server": "fixture", "tool": "echo", "arguments": {"text": "CTF{gateway_mcp}"}}),
            },
        }]}, "finish_reason": "tool_calls"}


class MCPLargeModel:
    model = "mcp-large-test"

    def __init__(self) -> None:
        self.turn = 0

    def chat_tools(self, messages, *, tools, temperature=0.2):
        self.turn += 1
        if self.turn == 1:
            call = {
                "id": "large_1",
                "type": "function",
                "function": {
                    "name": "mcp__fixture__large_result",
                    "arguments": json.dumps({"chars": 5000}),
                },
            }
        else:
            result = json.loads(next(item["content"] for item in reversed(messages) if item["role"] == "tool"))
            call = {
                "id": "finish_1",
                "type": "function",
                    "function": {"name": "finish_session", "arguments": json.dumps({
                        "summary": "MCP connectivity and large output persistence were verified.",
                        "evidence_artifact_ids": result["artifact_ids"],
                        "coverage": ["called the allowed fixture MCP method", "verified persisted output"],
                        "limitations": ["local fixture transport only"],
                    })},
            }
        return {"message": {"role": "assistant", "content": "", "tool_calls": [call]}, "finish_reason": "tool_calls"}


class CatalogTurnModel:
    model = "mcp-catalog-turn-test"

    def __init__(self) -> None:
        self.tool_names: list[list[str]] = []

    def chat_tools(self, messages, *, tools, temperature=0.2):
        names = [item["function"]["name"] for item in tools]
        self.tool_names.append(names)
        if len(self.tool_names) == 1:
            call = {"id": "refresh_call", "type": "function", "function": {"name": "mcp__fixture__echo", "arguments": json.dumps({"text": "done"})}}
        else:
            result = json.loads(next(item["content"] for item in reversed(messages) if item["role"] == "tool"))
            call = {"id": "refresh_finish", "type": "function", "function": {"name": "finish_session", "arguments": json.dumps({
                "summary": "Catalog refresh behavior was verified through a real MCP call.",
                "evidence_artifact_ids": result["artifact_ids"],
                "coverage": ["initial direct-tool catalog", "next-turn refreshed catalog"],
                "limitations": ["local fixture server only"],
            })}}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [call]}, "finish_reason": "tool_calls"}


class MultiCallModel:
    model = "mcp-multi-call-test"
    def __init__(self) -> None: self.turn = 0
    def chat_tools(self, messages, *, tools, temperature=0.2):
        self.turn += 1
        if self.turn == 1:
            calls = [
                {"id": "multi_a", "type": "function", "function": {"name": "mcp__fixture__echo", "arguments": json.dumps({"text": "alpha"})}},
                {"id": "multi_b", "type": "function", "function": {"name": "mcp__fixture__echo", "arguments": json.dumps({"text": "beta"})}},
            ]
        else:
            calls = [{"id": "multi_finish", "type": "function", "function": {"name": "finish_session", "arguments": json.dumps({"summary": "done"})}}]
        return {"message": {"role": "assistant", "content": "", "tool_calls": calls}, "finish_reason": "tool_calls"}


class RefreshAfterCallManager(MCPManager):
    def call_tool(self, **kwargs):
        outcome = super().call_tool(**kwargs)
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        payload["servers"]["fixture"]["visibility"]["allowMethods"] = ["large_result"]
        self.config_path.write_text(json.dumps(payload), encoding="utf-8")
        self.refresh()
        return outcome


class ImageOutcomeManager(MCPManager):
    def call_tool(self, **kwargs):
        route = kwargs["route"]
        trace_id = kwargs["trace_id"]
        encoded = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        return MCPCallOutcome(
            ok=True, server=route.server_id, method=route.method,
            trace_id=trace_id, request_id="mcp_image", catalog_version=kwargs["catalog_version"],
            content=[{"type": "image", "data": encoded, "mimeType": "image/png"}],
            raw_result={"content": [{"type": "image", "data": encoded, "mimeType": "image/png"}]},
            timings={"total_ms": 1},
        )


class MCPImageModel:
    model = "mcp-image-test"

    def __init__(self) -> None:
        self.turn = 0
        self.saw_image_block = False

    def chat_tools(self, messages, *, tools, temperature=0.2):
        self.turn += 1
        if self.turn == 1:
            call = {"id": "image_call", "type": "function", "function": {"name": "mcp__fixture__echo", "arguments": json.dumps({"text": "image"})}}
        else:
            self.saw_image_block = any(
                item.get("role") == "user"
                and isinstance(item.get("content"), list)
                and any(isinstance(block, dict) and block.get("type") == "image_url" for block in item["content"])
                for item in messages
            )
            result = json.loads(next(item["content"] for item in reversed(messages) if item.get("role") == "tool"))
            call = {"id": "image_finish", "type": "function", "function": {"name": "finish_session", "arguments": json.dumps({
                "summary": "Verified that the authorized MCP source returned an image.",
                "evidence_artifact_ids": result["artifact_ids"],
                "coverage": ["authorized MCP image source"],
                "limitations": ["one deterministic fixture image"],
            })}}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [call]}, "finish_reason": "tool_calls"}


def _config(tmp_path: Path, *, inline: int = 32000, artifact_limit: int = 20000) -> Path:
    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "servers": {
                    "fixture": {
                        "command": sys.executable,
                        "args": [str(fixture)],
                        "maxInlineChars": inline,
                        "maxArtifactBytes": artifact_limit,
                        "visibility": {"risk": "passive"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_mcp_image_result_becomes_real_model_image_block(tmp_path: Path, monkeypatch) -> None:
    run_root = tmp_path / "runs"
    task = TGATask(
        id="mcp_image", name="MCP image", mode="penetration_test",
        target="http://127.0.0.1", goal="inspect the image result",
        mcp_servers=["fixture"], mcp_direct_tools=["mcp__fixture__echo"],
    )
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    model = MCPImageModel()
    monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: model)
    mcp = ImageOutcomeManager(config_path=_config(tmp_path), cache_path=tmp_path / "cache.json")

    snapshot = Manager(store=store, run_root=run_root, executor=NeverExecutor(), mcp_manager=mcp).run_session(task.id)

    assert snapshot["session"]["status"] == "completed"
    assert model.saw_image_block is True
    transcript = json.loads((run_root / task.id / "solvers" / snapshot["solvers"][0]["id"] / "session" / "messages.json").read_text(encoding="utf-8"))
    image_messages = [item for item in transcript if isinstance(item.get("content"), list)]
    assert image_messages[0]["content"][1]["type"] == "image_url"
    tool_message = next(item for item in transcript if item.get("role") == "tool")
    assert "iVBOR" not in tool_message["content"]
    assert snapshot["artifacts"][0]["input_id"] is None
    store.close()


def test_agent_session_exposes_and_routes_native_mcp_tool(tmp_path: Path, monkeypatch) -> None:
    run_root = tmp_path / "runs"
    task = TGATask(
        id="mcp_agent",
        name="MCP Agent",
        mode="ctf",
        target="http://127.0.0.1",
        goal="find the flag",
        flag_format=r"CTF\{[^}]+\}",
        mcp_servers=["fixture"],
        mcp_direct_tools=["mcp__fixture__echo"],
    )
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    model = MCPFlagModel()
    monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: model)
    mcp = MCPManager(config_path=_config(tmp_path), cache_path=tmp_path / "cache.json")

    snapshot = Manager(store=store, run_root=run_root, executor=NeverExecutor(), mcp_manager=mcp).run_session(task.id)

    assert snapshot["session"]["status"] == "completed"
    assert any(item["function"]["name"] == "mcp__fixture__echo" for item in model.tools)
    assert snapshot["actions"][0]["capability"] == "mcp__fixture__echo"
    assert snapshot["flags"][0]["value"] == "CTF{dynamic_mcp}"
    end = next(event for event in snapshot["agent_events"] if event["type"] == "TOOL_EXECUTION_END")
    assert end["payload"]["mcp_server"] == "fixture"
    assert end["payload"]["trace_id"].startswith("trace_")
    assert end["payload"]["artifact_ids"]
    events_json = json.dumps(snapshot["agent_events"], ensure_ascii=False)
    assert "supersecret" not in events_json
    assert "[REDACTED]" in events_json
    artifact = next(item for item in snapshot["artifacts"] if item["id"] == end["payload"]["artifact_ids"][0])
    artifact_text = (run_root / task.id / "artifacts" / artifact["path"]).read_text(encoding="utf-8")
    assert "supersecret" not in artifact_text
    assert "[REDACTED]" in artifact_text
    store.close()


def test_agent_session_uses_one_gateway_without_direct_tool_injection(tmp_path: Path, monkeypatch) -> None:
    run_root = tmp_path / "runs"
    task = TGATask(id="mcp_gateway", name="MCP gateway", mode="ctf", target="http://127.0.0.1", goal="find flag", flag_format=r"CTF\{[^}]+\}", mcp_servers=["fixture"])
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    model = MCPGatewayFlagModel()
    monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: model)
    mcp = MCPManager(config_path=_config(tmp_path), cache_path=tmp_path / "cache.json")
    snapshot = Manager(store=store, run_root=run_root, executor=NeverExecutor(), mcp_manager=mcp).run_session(task.id)
    names = [item["function"]["name"] for item in model.tools]
    assert snapshot["session"]["status"] == "completed"
    assert "tga_mcp" in names
    assert "mcp__fixture__echo" not in names
    start = next(item for item in snapshot["agent_events"] if item["type"] == "TOOL_EXECUTION_START")
    end = next(item for item in snapshot["agent_events"] if item["type"] == "TOOL_EXECUTION_END")
    assert start["payload"]["llm_tool_name"] == "tga_mcp"
    assert start["payload"]["routed_tool_name"] == "mcp__fixture__echo"
    assert start["payload"]["trace_id"]
    assert end["payload"]["request_id"] and end["payload"]["mcp_request_id"] and end["payload"]["trace_id"]
    store.close()


def test_large_mcp_result_spills_and_transcript_stays_bounded(tmp_path: Path, monkeypatch) -> None:
    run_root = tmp_path / "runs"
    task = TGATask(
        id="mcp_large",
        name="MCP Large",
        mode="penetration_test",
        target="http://127.0.0.1",
        goal="inspect output",
        mcp_servers=["fixture"],
        mcp_direct_tools=["mcp__fixture__large_result"],
    )
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    model = MCPLargeModel()
    monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: model)
    mcp = MCPManager(config_path=_config(tmp_path, inline=512), cache_path=tmp_path / "cache.json")

    snapshot = Manager(store=store, run_root=run_root, executor=NeverExecutor(), mcp_manager=mcp).run_session(task.id)

    transcript_path = run_root / task.id / "solvers" / snapshot["solvers"][0]["id"] / "session" / "messages.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    first_tool = next(item for item in transcript if item.get("tool_call_id") == "large_1")
    payload = json.loads(first_tool["content"])
    assert payload["truncated"] is True
    assert payload["artifact_id"]
    assert len(first_tool["content"]) < 4000
    artifact = next(item for item in snapshot["artifacts"] if item["id"] == payload["artifact_id"])
    stored = (run_root / task.id / "artifacts" / artifact["path"]).read_text(encoding="utf-8")
    assert "x" * 5000 in stored
    store.close()


def test_catalog_refresh_becomes_visible_on_next_turn_only(tmp_path: Path, monkeypatch) -> None:
    run_root = tmp_path / "runs"
    task = TGATask(id="mcp_refresh", name="MCP Refresh", mode="penetration_test", target="http://127.0.0.1", goal="refresh", mcp_servers=["fixture"], mcp_direct_tools=["mcp__fixture__echo", "mcp__fixture__large_result"])
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    config_path = _config(tmp_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["servers"]["fixture"]["visibility"]["allowMethods"] = ["echo"]
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    model = CatalogTurnModel()
    monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: model)
    mcp = RefreshAfterCallManager(config_path=config_path, cache_path=tmp_path / "cache.json")

    snapshot = Manager(store=store, run_root=run_root, executor=NeverExecutor(), mcp_manager=mcp).run_session(task.id)

    assert snapshot["session"]["status"] == "completed"
    assert "mcp__fixture__echo" in model.tool_names[0]
    assert "mcp__fixture__large_result" not in model.tool_names[0]
    assert "mcp__fixture__large_result" in model.tool_names[1]
    assert "mcp__fixture__echo" not in model.tool_names[1]
    start = next(event for event in snapshot["agent_events"] if event["type"] == "TOOL_EXECUTION_START")
    end = next(event for event in snapshot["agent_events"] if event["type"] == "TOOL_EXECUTION_END")
    assert start["payload"]["catalog_version"] == end["payload"]["catalog_version"]
    store.close()


def test_artifact_hard_limit_is_explicit_and_enforced(tmp_path: Path, monkeypatch) -> None:
    run_root = tmp_path / "runs"
    task = TGATask(id="mcp_artifact_cap", name="MCP cap", mode="penetration_test", target="http://127.0.0.1", goal="cap", mcp_servers=["fixture"], mcp_direct_tools=["mcp__fixture__large_result"])
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    model = MCPLargeModel()
    monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: model)
    mcp = MCPManager(config_path=_config(tmp_path, inline=512, artifact_limit=1024), cache_path=tmp_path / "cache.json")
    snapshot = Manager(store=store, run_root=run_root, executor=NeverExecutor(), mcp_manager=mcp).run_session(task.id)
    end = next(event for event in snapshot["agent_events"] if event["type"] == "TOOL_EXECUTION_END")
    artifact = next(item for item in snapshot["artifacts"] if item["id"] == end["payload"]["artifact_ids"][0])
    path = run_root / task.id / "artifacts" / artifact["path"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path.stat().st_size <= 1024
    assert payload["artifact_truncated"] is True
    assert payload["original_bytes"] > payload["saved_bytes"]
    assert end["payload"]["error"]["code"] == "OUTPUT_TRUNCATED"
    store.close()


def test_multiple_mcp_results_remain_mapped_to_their_tool_call_ids(tmp_path: Path, monkeypatch) -> None:
    run_root = tmp_path / "runs"
    task = TGATask(id="mcp_multi", name="MCP multi", mode="penetration_test", target="http://127.0.0.1", goal="multi", mcp_servers=["fixture"], mcp_direct_tools=["mcp__fixture__echo"])
    store = EvidenceStore(run_root / task.id / "evidence.db")
    store.create_task(task)
    model = MultiCallModel()
    monkeypatch.setattr("tga.runtime.manager.build_model_client_from_env", lambda: model)
    mcp = MCPManager(config_path=_config(tmp_path), cache_path=tmp_path / "cache.json")
    snapshot = Manager(store=store, run_root=run_root, executor=NeverExecutor(), mcp_manager=mcp).run_session(task.id)
    transcript_path = run_root / task.id / "solvers" / snapshot["solvers"][0]["id"] / "session" / "messages.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    results = {item["tool_call_id"]: json.loads(item["content"]) for item in transcript if item.get("role") == "tool"}
    assert results["multi_a"]["content"][0]["text"] == "alpha"
    assert results["multi_b"]["content"][0]["text"] == "beta"
    end_by_call = {event["payload"]["tool_call_id"]: event for event in snapshot["agent_events"] if event["type"] == "TOOL_EXECUTION_END"}
    assert end_by_call["multi_a"]["payload"]["mcp_method"] == "echo"
    assert end_by_call["multi_b"]["payload"]["mcp_method"] == "echo"
    store.close()
