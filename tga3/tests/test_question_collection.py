import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from test_aoe_runtime import setup

from tga3.domain import AgentState, FinalCandidateBody, RunState
from tga3.errors import ContractError
from tga3.submissions import SubmissionRejected, Submissions, SubmissionSpec, accepted_receipt
from tga3.target_instances import TargetInstances
from tga3.workers.aoe_access import overview_policy, scout_post_tool_notice
from tga3.workers.blackboard_hook import checkpoint
from tga3.workers.scout_probe_guard import ScoutProbeGuard, build_no_proxy


async def test_outbox_import_preserves_ssot_raw_source_collection_notes_and_archives_files(tmp_path):
    cfg, storage, _, c, parent, _ = await setup(tmp_path)
    scout_ws = cfg.task_path(parent.id, "workspaces", "scout")
    outbox = scout_ws / "outbox"
    attach_dir = scout_ws / "attachments" / "web-01"
    outbox.mkdir(parents=True, exist_ok=True)
    attach_dir.mkdir(parents=True, exist_ok=True)
    (attach_dir / "app.zip").write_bytes(b"PK\x03\x04fixture")

    payload = {
        "source_key": "web-01",
        "title": "Easy Login",
        "description": "Original challenge description without model rewrite.",
        "difficulty": "easy",
        "category": "web",
        "tags": ["sqli", "login", "sqli"],
        "flag_format": "SEKAI{...}",
        "connection_info": "Dynamic target managed by platform",
        "submission_instructions": "POST /submit with unique_code and flag",
        "collection_notes": "Only public source zip was listed; runtime DB is inside the dynamic container.",
        "attachments": ["/workspace/attachments/web-01/app.zip"],
        "target": {
            "mode": "managed",
            "start": {
                "method": "POST",
                "url": "https://fixture.example/start",
                "headers": {"X-Token": "secret"},
                "params": {"unique_code": "web-01"},
            },
            "stop": {
                "method": "POST",
                "url": "https://fixture.example/stop",
                "headers": {"X-Token": "secret"},
                "params": {"unique_code": "web-01"},
            },
        },
        "submission": {
            "request": {
                "method": "POST",
                "url": "https://fixture.example/submit",
                "headers": {"X-Token": "secret"},
                "body": {"unique_code": "web-01", "flag": "{flag}"},
            },
            "success_path": "correct",
            "success_value": True,
        },
        "raw_source": {"unique_code": "web-01", "score": 150, "extra": {"author": "alice"}},
    }
    (outbox / "web-01.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    receipt = await c.aoe.collection.import_outbox(parent.id)
    assert receipt["saved"] is True
    assert receipt["imported_count"] == 1
    assert receipt["imported_keys"] == ["web-01"]
    assert not receipt["errors"]
    assert not list(outbox.glob("*.json"))
    assert (scout_ws / "imported" / "web-01.json").is_file()

    q = (await storage.list_questions(parent.id))[0]
    # SSoT: DB metadata holds target, submission, category, tags, and collection_notes.
    assert q.metadata["target"]["mode"] == "managed"
    assert q.metadata["submission"]["request"]["url"] == "https://fixture.example/submit"
    assert q.metadata["category"] == "web"
    assert q.metadata["tags"] == ["sqli", "login"]
    assert q.metadata["collection_notes"] == payload["collection_notes"]
    assert "source" not in q.metadata

    # Deleting derived question.json must not lose target/submission contracts on restart.
    (cfg.task_path(q.id) / "question.json").unlink()
    assert TargetInstances(cfg).spec(q).mode == "managed"
    assert Submissions(cfg).spec(q).request.url == "https://fixture.example/submit"

    # Raw source preserved verbatim on disk; collection_notes propagated to question blackboard.
    raw = json.loads((cfg.task_path(q.id) / "source.json").read_text(encoding="utf-8"))
    assert raw["extra"] == {"author": "alice"}
    entries = await storage.list_entries(q.id)
    desc_entry = next(e for e in entries if e.topic == "question")
    assert "收集备注：" in desc_entry.body["text"]
    assert "dynamic container" in desc_entry.body["text"]

    # Control-plane signal file replaces redundant questions.json.
    state_file = cfg.task_path(parent.id) / "collection-state.json"
    assert state_file.is_file()
    assert not (cfg.task_path(parent.id) / "questions.json").exists()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["imported_count"] == 1 and state["imported_keys"] == ["web-01"] and not state["collection_complete"]
    assert "description" not in json.dumps(state)

    finished = await c.aoe.finish_collection(parent.id, expected_count=1)
    assert finished["collection_complete"] is True and finished["status"] == "complete"


async def test_outbox_allows_attachment_only_question_and_isolates_invalid_files(tmp_path):
    cfg, storage, _, c, parent, _ = await setup(tmp_path)
    scout_ws = cfg.task_path(parent.id, "workspaces", "scout")
    outbox = scout_ws / "outbox"
    attach_dir = scout_ws / "attachments" / "pwn-01"
    outbox.mkdir(parents=True, exist_ok=True)
    attach_dir.mkdir(parents=True, exist_ok=True)
    (attach_dir / "chall").write_bytes(b"\x7fELFfixture")

    # Valid attachment-only question without description or explicit title.
    (outbox / "01-valid.json").write_text(json.dumps({
        "source_key": "pwn-01",
        "attachments": ["attachments/pwn-01/chall"],
        "collection_notes": "Archive only contained binary without text prompt.",
        "target": {"mode": "none"},
    }), encoding="utf-8")
    # Invalid: neither description nor attachments.
    (outbox / "02-empty.json").write_text(json.dumps({"source_key": "bad-empty"}), encoding="utf-8")
    # Invalid: broken JSON syntax.
    (outbox / "03-broken.json").write_text("{not-json", encoding="utf-8")
    # Invalid: attachment escapes workspace.
    (outbox / "04-escape.json").write_text(json.dumps({
        "source_key": "bad-path",
        "description": "attempt escape",
        "attachments": ["/workspace/../../etc/passwd"],
    }), encoding="utf-8")

    result = await c.aoe.collection.import_outbox(parent.id)
    assert result["imported_count"] == 1
    assert result["imported_keys"] == ["pwn-01"]
    assert len(result["imported"]) == 1
    assert len(result["errors"]) == 3
    assert not list(outbox.glob("*.json"))
    assert (scout_ws / "imported" / "01-valid.json").is_file()
    for bad in ("02-empty", "03-broken", "04-escape"):
        assert (scout_ws / "rejected" / f"{bad}.json").is_file()
        assert (scout_ws / "rejected" / f"{bad}.error.json").is_file()

    q = (await storage.list_questions(parent.id))[0]
    assert q.title == "pwn-01"
    assert len(await storage.list_input_files(q.id)) == 1


async def test_submission_spec_supports_custom_body_keys_int_ids_and_string_success_value(tmp_path):
    cfg, storage, _, c, parent, _ = await setup(tmp_path)
    outbox = cfg.task_path(parent.id, "workspaces", "scout") / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "ctfd-42.json").write_text(json.dumps({
        "source_key": "crypto-rsa",
        "title": "CTFd RSA",
        "description": "Solve RSA",
        "submission": {
            "request": {
                "method": "POST",
                "url": "https://ctfd.example/api/v1/challenges/attempt",
                "headers": {"Authorization": "Token ctfd-secret"},
                "body": {"challenge_id": 42, "submission": "{flag}"},
            },
            "success_path": "data.status",
            "success_value": "correct",
            "duplicate_code": "already_solved",
        },
    }), encoding="utf-8")
    await c.aoe.collection.import_outbox(parent.id)
    q = (await storage.list_questions(parent.id))[0]
    spec = Submissions(cfg).spec(q)
    assert isinstance(spec, SubmissionSpec)
    assert accepted_receipt(spec, {"success": True, "data": {"status": "correct"}}, 200)
    assert not accepted_receipt(spec, {"success": True, "data": {"status": "incorrect"}}, 200)

    # Simulate a Worker action log with an integer challenge_id and custom field name.
    log_dir = cfg.task_path(q.id, "logs", "worker-openai")
    ws_dir = cfg.task_path(q.id, "workspaces", "worker-openai")
    (ws_dir / "result").mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "result" / "wrong.txt").write_text('{"success": true, "data": {"status": "incorrect"}}', encoding="utf-8")
    (ws_dir / "result" / "right.txt").write_text('{"success": true, "data": {"status": "correct"}}', encoding="utf-8")
    actions = {
        "schema_version": 2,
        "task_id": str(q.id),
        "agent_id": "worker-openai",
        "actions": [
            {
                "action_id": "worker-openai-action-wrong",
                "tool_call_id": "tc-1",
                "status": "completed",
                "action": {
                    "tool": "exec_command",
                    "command": (
                        "curl -s -X POST https://ctfd.example/api/v1/challenges/attempt "
                        "-d '{\"challenge_id\": 42, \"submission\": \"flag{nope}\"}'"
                    ),
                },
                "artifact": {"output_path": "result/wrong.txt", "attachments": []},
            },
            {
                "action_id": "worker-openai-action-right",
                "tool_call_id": "tc-2",
                "status": "completed",
                "action": {
                    "tool": "exec_command",
                    "command": (
                        "curl -s -X POST https://ctfd.example/api/v1/challenges/attempt "
                        "-d '{\"challenge_id\": 42, \"submission\": \"flag{real}\"}'"
                    ),
                },
                "artifact": {"output_path": "result/right.txt", "attachments": []},
            },
        ],
    }
    (log_dir / "worker-openai-actions.json").write_text(json.dumps(actions), encoding="utf-8")

    subs = Submissions(cfg)
    with pytest.raises(SubmissionRejected):
        subs.validate(q, FinalCandidateBody(conclusion="flag{nope}", rationale="tried", finding_ids=[uuid4()]))
    accepted_id = subs.validate(
        q, FinalCandidateBody(conclusion="flag{real}", rationale="solved", finding_ids=[uuid4()])
    )
    assert accepted_id == "worker-openai-action-right"


def test_scout_access_allows_free_collection_blocks_scanners_and_trips_probe_guard(monkeypatch, tmp_path):
    aoe_root = tmp_path / "aoe"
    ws = tmp_path / "ws"
    aoe_root.mkdir()
    ws.mkdir()
    for k, v in {
        "TGA3_TASK_OVERVIEW_ROOT": "/task-overview",
        "TGA3_WORKSPACE": str(ws),
        "TGA3_AOE_ROOT": str(aoe_root),
        "TGA3_AOE_PARENT_ID": "parent",
        "TGA3_AGENT_ROLE": "scout",
        "TGA3_SCOUT_PROBE_MAX_MISSES": "3",
    }.items():
        monkeypatch.setenv(k, v)

    def ev(name, command, response="", tool="exec_command", session="s1"):
        return {
            "hook_event_name": name,
            "tool_name": tool,
            "session_id": session,
            "tool_input": {"cmd": command},
            "tool_response": response,
        }

    # Must read overview first, then free shell/python/curl tools are permitted.
    assert overview_policy(ev("PreToolUse", "curl https://ctf.example/challenges"))
    for name in ("scene.md", "task.md"):
        cmd = "cat /task-overview/" + name
        assert not overview_policy(ev("PreToolUse", cmd))
        overview_policy(ev("PostToolUse", cmd, "Full content"))
    assert not overview_policy(ev("PreToolUse", "curl -s https://ctf.example/api/challenges"))
    assert not overview_policy(ev("PreToolUse", "python3 collect.py"))

    # Directory/vulnerability scanners are blocked.
    for cmd in ("ffuf -u http://target/FUZZ -w list.txt", "gobuster dir -u http://target", "nmap -sV 10.0.0.1"):
        reason = overview_policy(ev("PreToolUse", cmd))
        assert reason and "forbidden" in reason.lower()

    # Simulate 1 imported question followed by repeated 404 misses -> trips probe guard.
    (aoe_root / "collection-state.json").write_text(
        json.dumps({"collection_complete": False, "imported_count": 1, "imported_keys": ["q1"]}),
        encoding="utf-8",
    )
    for i in range(3):
        overview_policy(ev("PostToolUse", f"curl -i https://ctf.example/guess-{i}", "HTTP/1.1 404 Not Found"))
    notice = scout_post_tool_notice(ev("PostToolUse", "curl -i https://ctf.example/guess-2", "HTTP/1.1 404 Not Found"))
    assert notice and "collection_notes" in notice

    # Network probing is paused once tripped with imported questions, while local finish tools remain allowed.
    blocked = overview_policy(ev("PreToolUse", "curl -i https://ctf.example/another-guess"))
    assert blocked and "paused" in blocked.lower()
    assert not overview_policy(ev("PreToolUse", "ls /workspace/outbox"))
    finish_tool = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__intel__scout_finish_collection",
        "session_id": "s1",
    }
    assert not overview_policy(finish_tool)

    # New imported question resets miss counter.
    (aoe_root / "collection-state.json").write_text(
        json.dumps({"collection_complete": False, "imported_count": 2, "imported_keys": ["q1", "q2"]}),
        encoding="utf-8",
    )
    overview_policy(ev("PostToolUse", "ls /workspace/outbox", "ok"))
    assert not overview_policy(ev("PreToolUse", "curl -i https://ctf.example/detail/2"))

    # Once collection_complete=True, every tool is denied immediately.
    (aoe_root / "collection-state.json").write_text(
        json.dumps({"collection_complete": True, "imported_count": 2, "imported_keys": ["q1", "q2"]}),
        encoding="utf-8",
    )
    assert "complete" in (overview_policy(ev("PreToolUse", "ls /workspace")) or "").lower()


async def test_http_probe_guard_proxy_allows_2xx_and_trips_on_distinct_404_enumeration(tmp_path):
    async def mock_upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/api/list", "/api/detail/1", "/files/chall.zip"}:
            return httpx.Response(200, json={"ok": True, "path": request.url.path})
        return httpx.Response(404, text="not found")

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_upstream))
    guard = ScoutProbeGuard(
        max_misses=3,
        bypass_hosts={"localhost", "control.internal"},
        state_path=tmp_path / "guard.json",
        client=upstream_client,
    )
    proxy_url = await guard.start()
    try:
        async with httpx.AsyncClient(proxy=proxy_url) as client:
            # Repeated 200 traffic is never throttled.
            for _ in range(10):
                resp = await client.get("http://ctf.example/api/list")
                assert resp.status_code == 200
            assert (await client.get("http://ctf.example/files/chall.zip")).status_code == 200

            # 3 distinct 404 paths trip the guard.
            for idx in range(3):
                resp = await client.get(f"http://ctf.example/guess/{idx}")
                assert resp.status_code == 404

            # Next unknown path is blocked with 429 and explicit guidance.
            blocked = await client.get("http://ctf.example/guess/extra")
            assert blocked.status_code == 429
            body = blocked.json()
            assert body["error"] == "probe_guard_tripped"
            assert "collection_notes" in body["message"]

            # Previously confirmed 200 paths remain accessible even after trip.
            still_good = await client.get("http://ctf.example/api/list")
            assert still_good.status_code == 200

            # Bypassed control-plane host is never tripped.
            for idx in range(5):
                bypass = await client.get(f"http://control.internal/missing/{idx}")
                assert bypass.status_code == 404
    finally:
        await guard.stop()
        await upstream_client.aclose()

    no_proxy = build_no_proxy("existing.local", {"localhost", "api.openai.com"})
    assert "existing.local" in no_proxy and "api.openai.com" in no_proxy and "localhost" in no_proxy


async def test_scout_partial_failure_preserves_collected_questions_and_keeps_workers_running(tmp_path):
    cfg, storage, containers, c, parent, _ = await setup(tmp_path)
    outbox = cfg.task_path(parent.id, "workspaces", "scout") / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)

    async def crashing_scout(spec, emit):
        (outbox / "q1.json").write_text(json.dumps({
            "source_key": "q1",
            "title": "Collected Before Crash",
            "description": "Solve me",
            "difficulty": "easy",
            "collection_notes": "Attachment missing from list API",
        }), encoding="utf-8")
        raise TimeoutError("simulated scout timeout")

    containers.scout = crashing_scout
    bindings = {
        run.agent_id: cfg.resolve_agent(run.agent_id, run.provider_id, run.model_id, run.protocol)
        for run in await storage.list_agents(parent.id)
    }
    await c.aoe._scout(parent.id, bindings)

    refreshed = await storage.get_task(parent.id)
    assert refreshed.state == RunState.RUNNING
    assert refreshed.metadata["collection_complete"] is True
    assert refreshed.metadata["collection_status"] == "partial"
    assert refreshed.metadata["collection_warnings"]
    assert refreshed.metadata["scout_complete"] is True
    scout_run = await storage.get_agent(parent.id, "scout")
    assert scout_run.actual_state == AgentState.COMPLETED
    questions = await storage.list_questions(parent.id)
    assert len(questions) == 1 and questions[0].metadata["source_key"] == "q1"

    # Tick should dispatch the collected question to a Worker normally.
    await c.aoe.tick(parent.id)
    assert len(containers.launched) == 1 and containers.launched[0].task_id == questions[0].id


async def test_scout_failure_with_zero_questions_raises_contract_error(tmp_path):
    cfg, storage, containers, c, parent, _ = await setup(tmp_path)

    async def empty_failing_scout(spec, emit):
        raise RuntimeError("platform unreachable")

    containers.scout = empty_failing_scout
    bindings = {
        run.agent_id: cfg.resolve_agent(run.agent_id, run.provider_id, run.model_id, run.protocol)
        for run in await storage.list_agents(parent.id)
    }
    with pytest.raises(ContractError, match="platform unreachable"):
        await c.aoe._scout(parent.id, bindings)


def test_outbox_hook_auto_syncs_and_delivers_receipts(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    aoe_root = tmp_path / "aoe"
    (ws / "outbox").mkdir(parents=True)
    aoe_root.mkdir()
    (ws / "outbox" / "q1.json").write_text(json.dumps({"source_key": "q1", "description": "d"}), encoding="utf-8")
    for k, v in {
        "TGA3_AGENT_ROLE": "scout",
        "TGA3_AGENT_ID": "scout",
        "TGA3_TASK_ID": str(uuid4()),
        "TGA3_AOE_PARENT_ID": str(uuid4()),
        "TGA3_WORKSPACE": str(ws),
        "TGA3_AOE_ROOT": str(aoe_root),
        "TGA3_TASK_OVERVIEW_ROOT": "/task-overview",
        "TGA3_BLACKBOARD_MCP_URL": "http://127.0.0.1:8083/mcp",
    }.items():
        monkeypatch.setenv(k, v)

    posted = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "context": "",
                "delivery_id": "",
                "question_import_receipts": {"saved": True, "imported_count": 1, "imported_keys": ["q1"]},
            }

    class FakeClient:
        def post(self, url, json, timeout):
            posted.append((url, json))
            return FakeResponse()

    result = checkpoint(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "exec_command",
            "tool_input": {"cmd": "python3 collect.py"},
            "tool_response": "wrote /workspace/outbox/q1.json",
        },
        client=FakeClient(),
    )
    assert len(posted) == 1
    assert posted[0][1]["outbox_sync"] is True
    assert "question-import receipts" in result["hookSpecificOutput"]["additionalContext"]


async def test_outbox_watcher_streams_questions_to_workers_while_scout_is_still_running(tmp_path):
    cfg, storage, containers, c, parent, _ = await setup(tmp_path)
    outbox = cfg.task_path(parent.id, "workspaces", "scout") / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    release = asyncio.Event()
    wrote_first = asyncio.Event()

    async def streaming_scout(spec, emit):
        (outbox / "easy-1.json").write_text(json.dumps({
            "source_key": "easy-1",
            "title": "Easy One",
            "description": "Solve me first",
            "difficulty": "easy",
            "target": {"mode": "none"},
        }), encoding="utf-8")
        wrote_first.set()
        await release.wait()
        (outbox / "hard-2.json").write_text(json.dumps({
            "source_key": "hard-2",
            "title": "Hard Two",
            "description": "Solve me second",
            "difficulty": "hard",
            "target": {"mode": "none"},
        }), encoding="utf-8")
        await c.aoe.finish_collection(parent.id, expected_count=2)

    containers.scout = streaming_scout
    bindings = {
        run.agent_id: cfg.resolve_agent(run.agent_id, run.provider_id, run.model_id, run.protocol)
        for run in await storage.list_agents(parent.id)
    }
    scout_job = asyncio.create_task(c.aoe._scout(parent.id, bindings))
    try:
        await asyncio.wait_for(wrote_first.wait(), timeout=2)
        async with asyncio.timeout(3):
            while len(await storage.list_questions(parent.id)) < 1:
                await asyncio.sleep(0.05)
        assert not (await storage.get_task(parent.id)).metadata.get("collection_complete")
        await c.aoe.tick(parent.id)
        assert len(containers.launched) == 1
        release.set()
        await asyncio.wait_for(scout_job, timeout=3)
        assert len(await storage.list_questions(parent.id)) == 2
        assert (await storage.get_task(parent.id)).metadata["collection_complete"] is True
    finally:
        release.set()
        if not scout_job.done():
            scout_job.cancel()
        await asyncio.gather(scout_job, return_exceptions=True)


async def test_scout_stall_watchdog_warns_then_exits_cleanly_when_questions_already_imported(monkeypatch, tmp_path):
    from tga3.workers import aoe_scout
    from tga3.workers.action_journal import ActionJournal

    workspace, batch = tmp_path / "workspace", tmp_path / "batch"
    workspace.mkdir()
    batch.mkdir()
    (batch / "collection-state.json").write_text(json.dumps({
        "collection_complete": False,
        "status": "collecting",
        "imported_count": 2,
        "imported_keys": ["q1", "q2"],
    }), encoding="utf-8")
    for k, v in {
        "TGA3_AOE_PARENT_ID": str(uuid4()),
        "TGA3_WORKSPACE": str(workspace),
        "TGA3_AOE_ROOT": str(batch),
        "TGA3_SYSTEM_PROMPT": "collect",
        "TGA3_SCOUT_PHASE": "collecting",
        "TGA3_DISABLE_SCOUT_PROBE_GUARD": "1",
    }.items():
        monkeypatch.setenv(k, v)

    prompts_seen: list[str] = []

    class StallingAdapter:
        api_key = "secret"

        def __init__(self):
            self.journal = ActionJournal(tmp_path / "logs", identity={"agent_id": "scout"})
            self.timeout = None

        async def run_cycle(self, prompt, emit):
            prompts_seen.append(prompt)
            return "still thinking without importing"

    monkeypatch.setattr(aoe_scout, "CodexAdapter", StallingAdapter)
    state = workspace / ".tga3"
    state.mkdir()
    ident = uuid4().hex
    (state / "aoe-scout-request.json").write_text(json.dumps({"id": ident, "phase": "collecting"}))
    job = asyncio.create_task(aoe_scout.scout())
    try:
        result = state / "aoe-scout-results" / (ident + ".json")
        async with asyncio.timeout(3):
            while not result.exists():
                await asyncio.sleep(0.01)
        payload = json.loads(result.read_text(encoding="utf-8"))
        assert payload["finish_reason"] == "completed"
        assert len(prompts_seen) == 2
        assert "No new questions were imported recently" in prompts_seen[1]
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)


async def test_remote_attachments_downloaded_on_worker_claim_not_during_collection(tmp_path):
    cfg, storage, containers, c, parent, _ = await setup(tmp_path)
    downloads: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        downloads.append(str(req.url))
        if req.url.path == "/files/app.zip":
            assert req.headers.get("Authorization") == "Bearer dl-secret"
            return httpx.Response(
                200,
                content=b"PK\x03\x04remote-zip",
                headers={"Content-Disposition": 'attachment; filename="challenge-src.zip"'},
            )
        return httpx.Response(404, text="missing")

    c.aoe.attachment_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        outbox = cfg.task_path(parent.id, "workspaces", "scout") / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / "remote-q.json").write_text(
            json.dumps(
                {
                    "source_key": "remote-q",
                    "title": "Remote Attachment Challenge",
                    "description": "Analyze the downloaded zip.",
                    "difficulty": "easy",
                    "attachments": [
                        {
                            "url": "https://ctf.example/files/app.zip",
                            "headers": {"Authorization": "Bearer dl-secret"},
                        },
                        "https://ctf.example/files/missing.bin",
                    ],
                    "target": {"mode": "none"},
                }
            ),
            encoding="utf-8",
        )
        await c.aoe.collection.import_outbox(parent.id)
        await c.aoe.finish_collection(parent.id, expected_count=1)
        # Zero downloads during Scout collection phase!
        assert downloads == []
        q = (await storage.list_questions(parent.id))[0]
        assert len(await storage.list_input_files(q.id)) == 0

        # Worker claim triggers automatic host download before launching container.
        await c.aoe.tick(parent.id)
        assert len(containers.launched) == 1
        assert "https://ctf.example/files/app.zip" in downloads
        files = await storage.list_input_files(q.id)
        assert len(files) == 1
        assert files[0].name == "challenge-src.zip"
        saved_zip = cfg.task_path(q.id, "inputs") / f"{files[0].id}-challenge-src.zip"
        assert saved_zip.read_bytes() == b"PK\x03\x04remote-zip"
        # Missing attachment publishes a helpful notice without blocking Worker launch.
        entries = await storage.list_entries(q.id)
        assert any(e.kind == "user_file" and e.body["name"] == "challenge-src.zip" for e in entries)
        assert any(e.topic == "system" and "missing.bin" in e.body.get("text", "") for e in entries)

        # Second Worker attempt must NOT redownload already-saved attachment.
        before_count = downloads.count("https://ctf.example/files/app.zip")
        await c.aoe._prepare_attachments(await storage.get_task(q.id))
        assert downloads.count("https://ctf.example/files/app.zip") == before_count
    finally:
        await c.aoe.attachment_client.aclose()


async def test_already_solved_questions_marked_pre_solved_skip_workers_attachments_and_reporter(tmp_path):
    cfg, storage, containers, c, parent, _ = await setup(tmp_path)
    downloads: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        downloads.append(str(req.url))
        return httpx.Response(200, content=b"should-not-download")

    c.aoe.attachment_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        outbox = cfg.task_path(parent.id, "workspaces", "scout") / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / "old-solved.json").write_text(
            json.dumps(
                {
                    "source_key": "old-solved",
                    "title": "Already Solved On Platform",
                    "description": "",
                    "already_solved": True,
                    "attachments": ["https://ctf.example/files/big.iso"],
                    "collection_notes": "Platform already shows is_solved=true before task start.",
                }
            ),
            encoding="utf-8",
        )
        (outbox / "fresh-q.json").write_text(
            json.dumps(
                {
                    "source_key": "fresh-q",
                    "title": "Unsolved Challenge",
                    "description": "Please solve me",
                    "difficulty": "easy",
                    "target": {"mode": "none"},
                }
            ),
            encoding="utf-8",
        )
        await c.aoe.collection.import_outbox(parent.id)
        await c.aoe.finish_collection(parent.id, expected_count=2)
        await c.aoe.tick(parent.id)

        questions = {q.metadata["source_key"]: q for q in await storage.list_questions(parent.id)}
        pre = questions["old-solved"]
        fresh = questions["fresh-q"]

        assert pre.state == RunState.COMPLETED
        assert pre.metadata["status"] == "solved"
        assert pre.metadata["pre_solved"] is True
        assert pre.metadata["produce_report"] is False
        assert pre.metadata["attempts"] == []
        assert not downloads
        # Only the unsolved question is dispatched to a Worker.
        assert [spec.task_id for spec in containers.launched] == [fresh.id]

        snapshot = await c.aoe.snapshot(parent.id)
        pre_view = next(item for item in snapshot["questions"] if item["source_key"] == "old-solved")
        assert pre_view["pre_solved"] is True
        assert pre_view["report_state"] == "pre_solved"
        assert pre_view["status"] == "solved"
    finally:
        await c.aoe.attachment_client.aclose()


def test_worker_direct_submission_blocked_by_hook(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    (ws / ".tga3").mkdir(parents=True)
    submit_url = "https://ctf.example/api/v1/submit"
    (ws / ".tga3" / "submission.json").write_text(
        json.dumps({"managed_by_system": True, "blocked_url": submit_url, "flag_format": "flag{...}"}),
        encoding="utf-8",
    )
    for k, v in {
        "TGA3_TASK_OVERVIEW_ROOT": "/task-overview",
        "TGA3_WORKSPACE": str(ws),
        "TGA3_AOE_PARENT_ID": "parent",
        "TGA3_AGENT_ROLE": "worker",
        "TGA3_AOE_AUTO_SUBMIT_FLAG": "1",
    }.items():
        monkeypatch.setenv(k, v)

    def ev(name, command, response="", session="w1"):
        return {
            "hook_event_name": name,
            "tool_name": "exec_command",
            "session_id": session,
            "tool_input": {"cmd": command},
            "tool_response": response,
        }

    for name in ("scene.md", "task.md"):
        overview_policy(ev("PostToolUse", "cat /task-overview/" + name, "full content"))

    # Normal target interaction is allowed.
    assert not overview_policy(ev("PreToolUse", "curl -s http://10.0.0.5:8080/login"))
    # Direct platform flag submission via curl or python is blocked!
    reason_curl = overview_policy(ev("PreToolUse", f"curl -X POST {submit_url} -d '{{\"flag\":\"flag{{x}}\"}}'"))
    assert reason_curl and "final-candidate.json" in reason_curl
    reason_py = overview_policy(
        ev("PreToolUse", f"python3 -c \"import requests; requests.post('{submit_url}', json={{'flag':'x'}})\"")
    )
    assert reason_py and "forbidden" in reason_py.lower()


async def test_outbox_aggregates_target_request_string_errors_and_skips_existing_idempotently(tmp_path):
    cfg, storage, _, c, parent, _ = await setup(tmp_path)
    scout_ws = cfg.task_path(parent.id, "workspaces", "scout")
    outbox = scout_ws / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)

    (outbox / "00-static.json").write_text(
        json.dumps(
            {
                "source_key": "static-1",
                "title": "Static One",
                "description": "Original text",
                "target": {"mode": "none"},
            }
        ),
        encoding="utf-8",
    )
    for idx in range(1, 4):
        (outbox / f"dyn-{idx}.json").write_text(
            json.dumps(
                {
                    "source_key": f"dyn-{idx}",
                    "title": f"Dynamic {idx}",
                    "description": "Needs container",
                    "target": {
                        "mode": "managed",
                        "start": "https://ctf.example/api/startChallenge",
                        "stop": "https://ctf.example/api/stopChallenge",
                    },
                }
            ),
            encoding="utf-8",
        )

    first = await c.aoe.collection.import_outbox(parent.id)
    assert first["import_seq"] == 1
    assert first["imported_count"] == 1
    assert first["rejected_count"] == 3
    assert len(first["error_summary"]) == 1
    grouped = first["error_summary"][0]
    assert grouped["count"] == 3
    assert len(grouped["sample_files"]) == 3
    assert '{"method":"POST","url":"https://..."' in grouped["error"]

    state = json.loads((cfg.task_path(parent.id) / "collection-state.json").read_text(encoding="utf-8"))
    assert state["import_seq"] == 1
    assert state["rejected_count"] == 3
    assert state["last_batch"] == {"imported": 1, "skipped_existing": 0, "rejected": 3}
    assert state["recent_errors"][0]["count"] == 3

    # Re-emitting an already-registered source_key skips idempotently instead of ConflictError.
    (outbox / "00-static-again.json").write_text(
        json.dumps(
            {
                "source_key": "static-1",
                "title": "Static One",
                "description": "Re-generated wording",
                "target": {"mode": "none"},
            }
        ),
        encoding="utf-8",
    )
    (outbox / "dyn-1-fixed.json").write_text(
        json.dumps(
            {
                "source_key": "dyn-1",
                "title": "Dynamic 1",
                "description": "Needs container",
                "target": {
                    "mode": "managed",
                    "start": {"method": "POST", "url": "https://ctf.example/api/startChallenge", "body": {"id": 1}},
                    "stop": {"method": "POST", "url": "https://ctf.example/api/stopChallenge", "body": {"id": 1}},
                },
            }
        ),
        encoding="utf-8",
    )
    second = await c.aoe.collection.import_outbox(parent.id)
    assert second["import_seq"] == 2
    assert second["imported_count"] == 2
    assert second["skipped_count"] == 1
    assert not second["errors"]
    assert not second["error_summary"]
    assert (scout_ws / "imported" / "00-static-again.json").is_file()
    assert not (scout_ws / "rejected" / "00-static-again.json").exists()

    state2 = json.loads((cfg.task_path(parent.id) / "collection-state.json").read_text(encoding="utf-8"))
    assert state2["import_seq"] == 2
    assert state2["recent_errors"] == []
    assert state2["last_batch"] == {"imported": 1, "skipped_existing": 1, "rejected": 0}


def test_background_outbox_import_delivers_grouped_errors_on_next_post_tool_without_duplication(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    aoe_root = tmp_path / "aoe"
    (ws / "outbox").mkdir(parents=True)
    aoe_root.mkdir()
    for k, v in {
        "TGA3_AGENT_ROLE": "scout",
        "TGA3_AGENT_ID": "scout",
        "TGA3_TASK_ID": str(uuid4()),
        "TGA3_AOE_PARENT_ID": str(uuid4()),
        "TGA3_WORKSPACE": str(ws),
        "TGA3_AOE_ROOT": str(aoe_root),
        "TGA3_TASK_OVERVIEW_ROOT": "/task-overview",
        "TGA3_BLACKBOARD_MCP_URL": "http://127.0.0.1:8083/mcp",
    }.items():
        monkeypatch.setenv(k, v)

    grouped_err = [
        {
            "error": 'target.start: Input should be a valid dictionary. Example: {"method":"POST","url":"https://..."}',
            "count": 48,
            "sample_files": ["/workspace/outbox/dyn-1.json", "/workspace/outbox/dyn-2.json"],
        }
    ]
    # Simulate background watch_outbox() already emptying outbox/ and updating collection-state.json.
    (aoe_root / "collection-state.json").write_text(
        json.dumps(
            {
                "collection_complete": False,
                "imported_count": 49,
                "imported_keys": [f"q-{i}" for i in range(49)],
                "import_seq": 1,
                "rejected_count": 48,
                "last_batch": {"imported": 49, "skipped_existing": 0, "rejected": 48},
                "recent_errors": grouped_err,
            }
        ),
        encoding="utf-8",
    )

    ev = {
        "hook_event_name": "PostToolUse",
        "tool_name": "exec_command",
        "tool_input": {"cmd": "python3 export.py"},
        "tool_response": "exported 97 files",
    }
    first = checkpoint(ev)
    ctx1 = first["hookSpecificOutput"]["additionalContext"]
    assert "[AOE Collection Import Update #1]" in ctx1
    assert "48 files" in ctx1
    assert "target.start" in ctx1

    # Next PostToolUse with same import_seq must NOT duplicate the update.
    second = checkpoint(ev)
    ctx2 = second.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "[AOE Collection Import Update #1]" not in ctx2

    # Second batch with the exact same grouped error triggers repeat-error warning.
    (aoe_root / "collection-state.json").write_text(
        json.dumps(
            {
                "collection_complete": False,
                "imported_count": 49,
                "imported_keys": [f"q-{i}" for i in range(49)],
                "import_seq": 2,
                "rejected_count": 96,
                "last_batch": {"imported": 0, "skipped_existing": 49, "rejected": 48},
                "recent_errors": grouped_err,
            }
        ),
        encoding="utf-8",
    )
    third = checkpoint(ev)
    ctx3 = third["hookSpecificOutput"]["additionalContext"]
    assert "[AOE Collection Import Update #2]" in ctx3
    assert "repeated 2 times" in ctx3

    # Synchronous outbox_sync with import_seq=3 must advance last_seen_import_seq and not duplicate.
    (ws / "outbox" / "q50.json").write_text(json.dumps({"source_key": "q50", "description": "ok"}), encoding="utf-8")

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            (aoe_root / "collection-state.json").write_text(
                json.dumps(
                    {
                        "collection_complete": False,
                        "imported_count": 50,
                        "imported_keys": [f"q-{i}" for i in range(50)],
                        "import_seq": 3,
                        "rejected_count": 96,
                        "last_batch": {"imported": 1, "skipped_existing": 0, "rejected": 0},
                        "recent_errors": [],
                    }
                ),
                encoding="utf-8",
            )
            (ws / "outbox" / "q50.json").unlink()
            return {
                "context": "",
                "delivery_id": "",
                "question_import_receipts": {"saved": True, "import_seq": 3, "imported_count": 50},
            }

    class FakeClient:
        def post(self, url, json, timeout):
            return FakeResp()

    fourth = checkpoint(ev, client=FakeClient())
    ctx4 = fourth["hookSpecificOutput"]["additionalContext"]
    assert "TGA system question-import receipts:" in ctx4
    assert "[AOE Collection Import Update #3]" not in ctx4
    fifth = checkpoint(ev)
    ctx5 = fifth.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "[AOE Collection Import Update #3]" not in ctx5


def test_scout_single_cycle_stall_watchdog_blocks_noop_probes_but_allows_fixes_and_finish(monkeypatch, tmp_path):
    aoe_root = tmp_path / "aoe"
    ws = tmp_path / "ws"
    aoe_root.mkdir()
    ws.mkdir()
    for k, v in {
        "TGA3_TASK_OVERVIEW_ROOT": "/task-overview",
        "TGA3_WORKSPACE": str(ws),
        "TGA3_AOE_ROOT": str(aoe_root),
        "TGA3_AOE_PARENT_ID": "parent",
        "TGA3_AGENT_ROLE": "scout",
        "TGA3_AOE_COLLECTION_STALL_SECONDS": "600",
    }.items():
        monkeypatch.setenv(k, v)

    def ev(name, command, response="", tool="exec_command", session="s1"):
        return {
            "hook_event_name": name,
            "tool_name": tool,
            "session_id": session,
            "tool_input": {"cmd": command},
            "tool_response": response,
        }

    for name in ("scene.md", "task.md"):
        overview_policy(ev("PostToolUse", "cat /task-overview/" + name, "full content"))

    (aoe_root / "collection-state.json").write_text(
        json.dumps(
            {
                "collection_complete": False,
                "imported_count": 49,
                "imported_keys": [f"q-{i}" for i in range(49)],
                "import_seq": 1,
                "rejected_count": 48,
                "recent_errors": [{"error": "target.start must be dict", "count": 48, "sample_files": ["dyn-1.json"]}],
            }
        ),
        encoding="utf-8",
    )
    # Record initial progress at 49 imported questions.
    overview_policy(ev("PostToolUse", "python3 export.py", "done"))
    scout_post_tool_notice(ev("PostToolUse", "python3 export.py", "done"))

    # 4 no-op commands (each +2 idle_tool_calls -> 8) emit stall warning.
    for cmd in ("echo ok", "true", "ls /workspace/outbox", "ls /workspace/outbox | wc -l"):
        overview_policy(ev("PostToolUse", cmd, "0"))
    notice = scout_post_tool_notice(ev("PostToolUse", "echo ok", "ok"))
    assert notice and "Stall warning" in notice and "target.start must be dict" in notice

    # 2 more no-op commands (+4 -> 12) lock out no-ops and network probing.
    for cmd in ("echo hello", "true"):
        overview_policy(ev("PostToolUse", cmd, "ok"))
    scout_post_tool_notice(ev("PostToolUse", "true", ""))

    blocked_noop = overview_policy(ev("PreToolUse", "echo ok"))
    assert blocked_noop and "stalled" in blocked_noop.lower()
    blocked_ls = overview_policy(ev("PreToolUse", "ls /workspace/outbox"))
    assert blocked_ls and "stalled" in blocked_ls.lower()
    blocked_net = overview_policy(ev("PreToolUse", "curl -s https://ctf.example/api/challenges"))
    assert blocked_net and "stalled" in blocked_net.lower()

    # Local conversion script fix and scout_finish_collection remain allowed!
    assert not overview_policy(ev("PreToolUse", "python3 fix_export.py"))
    assert not overview_policy(
        {"hook_event_name": "PreToolUse", "tool_name": "mcp__intel__scout_finish_collection", "session_id": "s1"}
    )

    # Importing a new question resets stall state cleanly.
    (aoe_root / "collection-state.json").write_text(
        json.dumps(
            {
                "collection_complete": False,
                "imported_count": 50,
                "imported_keys": [f"q-{i}" for i in range(50)],
                "import_seq": 2,
                "rejected_count": 48,
                "recent_errors": [],
            }
        ),
        encoding="utf-8",
    )
    overview_policy(ev("PostToolUse", "python3 fix_export.py", "wrote dyn-1"))
    assert not overview_policy(ev("PreToolUse", "curl -s https://ctf.example/api/challenges"))
