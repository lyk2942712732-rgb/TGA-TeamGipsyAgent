import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tga3.hosted import gateway_url, platform_prompt, prepare_config
from tga3.process_runtime import ProcessPool, WorkerProcess
from tga3.workers.extra_cli import QwenAdapter
from tga3.workers.local_paths import local_paths


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://api.deepseek.com/v1", "http://api.deepseek.com.tsecbench.gw/v1"),
        (
            "https://example.maas.aliyuncs.com/apps/anthropic",
            "http://example.maas.aliyuncs.com.tsecbench.gw/apps/anthropic",
        ),
        ("http://api.deepseek.com.tsecbench.gw/v1/", "http://api.deepseek.com.tsecbench.gw/v1"),
    ],
)
def test_gateway_rewrites_only_model_endpoint(source, expected):
    assert gateway_url(source) == expected


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/a",
        "https://u:p@api.deepseek.com",
        "https://api.deepseek.com?key=a",
        "https://api.deepseek.com:443/v1",
        "https://api.deepseek.com/#a",
    ],
)
def test_gateway_rejects_ambiguous_endpoints(url):
    with pytest.raises(ValueError):
        gateway_url(url)


def test_local_paths_resolve_all_output_and_evidence_paths_without_touching_target_urls(monkeypatch):
    monkeypatch.setenv(
        "TGA3_LOCAL_PATHS",
        json.dumps(
            {
                "/workspace": "/app/data/workspaces/a",
                "/task-overview": "/app/data/overview",
                "/findings": "/app/data/findings/a",
            }
        ),
    )
    text = 'cat /task-overview/task.md; write "/workspace/final-candidate.json" and /findings/a.json'
    mapped = local_paths(text)
    assert mapped == (
        'cat /app/data/overview/task.md; write "/app/data/workspaces/a/final-candidate.json" '
        "and /app/data/findings/a/a.json"
    )
    assert local_paths(mapped) == mapped
    assert (
        local_paths("http://target/workspace/a /workspace-other /tmp/workspace/a")
        == "http://target/workspace/a /workspace-other /tmp/workspace/a"
    )
    assert local_paths({"hookSpecificOutput": {"additionalContext": "read /workspace/a"}}) == {
        "hookSpecificOutput": {"additionalContext": "read /app/data/workspaces/a/a"}
    }


def test_qwen_offline_settings_do_not_block_target_http_commands(monkeypatch, tmp_path):
    from test_aoe_sessions import environment

    environment(monkeypatch, tmp_path)
    monkeypatch.setenv("TGA3_HOSTED", "1")
    monkeypatch.setenv("TGA3_PROVIDER_PROTOCOL", "anthropic")
    adapter = QwenAdapter()
    _, env = adapter.command()
    settings = json.loads((Path(env["QWEN_HOME"]) / "settings.json").read_text())
    # In Qwen 0.27, a broad web_fetch deny also classifies curl as forbidden.
    # The platform enforces Internet isolation; target submission must remain usable.
    assert "web_fetch" not in settings["tools"]["exclude"]


def test_prepare_config_uses_runtime_credentials_gateway_urls_and_private_fresh_data(tmp_path, monkeypatch):
    for name, value in {
        "CODEX_BASE_URL": "https://provider.maas.aliyuncs.com/compatible-mode/v1",
        "CODEX_MODEL": "responses-model",
        "CODEX_API_KEY": "fixture-codex",
        "CLAUDE_BASE_URL": "https://provider.maas.aliyuncs.com/apps/anthropic",
        "CLAUDE_MODEL": "messages-model",
        "CLAUDE_API_KEY": "fixture-claude",
        "CHAT_BASE_URL": "https://api.deepseek.com/v1",
        "CHAT_MODEL": "chat-model",
        "CHAT_API_KEY": "fixture-chat",
        "BENCHMARK_BASE_URL": "http://platform.internal",
        "BENCHMARK_TOKEN": "fixture-benchmark",
    }.items():
        monkeypatch.setenv(name, value)
    if not hasattr(os, "getuid"):
        monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)
        monkeypatch.setattr(os, "getgid", lambda: 1000, raising=False)
    templates = tmp_path / "templates"
    templates.mkdir()
    project = Path(__file__).parents[1]
    for name in ["agents.json", "scenes.json", "runtime.json"]:
        shutil.copyfile(project / "config" / name, templates / name)
    shutil.copyfile(project / "schema.sql", templates / "schema.sql")
    config = prepare_config(tmp_path / "runtime", templates)
    for agent in config.agents.agents:
        resolved = config.resolve_agent(agent)
        assert ".tsecbench.gw" in resolved.provider.sdk_base_url(resolved.protocol)
        assert resolved.api_key.get_secret_value().startswith("fixture-")
    assert config.runtime.ctf_knowledge_mcp_url == ""
    assert not list(config.resolve_path(config.runtime.task_root).iterdir())
    prompt = platform_prompt()
    assert "http://platform.internal/openapi/v1/challenges" in prompt
    assert "platform.internal.tsecbench.gw" not in prompt
    assert "fixture-benchmark" in prompt
    assert not (templates / "models.json").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Hosted container runtime is Linux")
@pytest.mark.parametrize("source", ["none", "local"])
def test_process_pool_has_separate_workspaces_homes_and_never_inherits_controller_secrets(tmp_path, monkeypatch, source):
    fake = tmp_path / "fake/tga3/workers"
    fake.mkdir(parents=True)
    (fake.parent / "__init__.py").touch()
    (fake / "__init__.py").touch()
    (fake / "openai_worker.py").write_text(
        "import json,os,pathlib; pathlib.Path('observed.json').write_text(json.dumps(dict(os.environ)))"
    )
    monkeypatch.setenv("PYTHONPATH", str(fake.parents[1]))
    monkeypatch.setenv("BENCHMARK_TOKEN", "controller-secret")
    pool = ProcessPool()
    processes = []
    for ident in ["a", "b"]:
        workspace = tmp_path / ident
        workspace.mkdir()
        logs = tmp_path / (ident + "-logs")
        inputs = tmp_path / (ident + "-inputs")
        for root in [logs, inputs]:
            root.mkdir()
        processes.append(
            pool.run(
                environment={"TGA3_AGENT_RUNTIME": "codex_cli", "TGA3_AGENT_ROLE": "worker",
                             "TGA3_KNOWLEDGE_SOURCE": source,
                             "TGA3_CTF_KNOWLEDGE_MCP_URL": "https://not-allowed.example/mcp",
                             "TGA3_WEB_SEARCH_MCP_URL": "https://not-allowed.example/search"},
                volumes={
                    str(workspace): {"bind": "/workspace"},
                    str(logs): {"bind": "/logs"},
                    str(inputs): {"bind": "/inputs"},
                },
                labels={"agent": ident},
            )
        )
    for process in processes:
        assert process.process.wait(timeout=10) == 0
    observed = [json.loads((tmp_path / ident / "observed.json").read_text()) for ident in ["a", "b"]]
    assert observed[0]["HOME"] != observed[1]["HOME"]
    assert observed[0]["TGA3_WORKSPACE"] != observed[1]["TGA3_WORKSPACE"]
    assert all("BENCHMARK_TOKEN" not in env for env in observed)
    expected = "http://127.0.0.1:8083/local-knowledge-mcp/" if source == "local" else ""
    assert all(env["TGA3_CTF_KNOWLEDGE_MCP_URL"] == expected for env in observed)
    assert all(env["TGA3_WEB_SEARCH_MCP_URL"] == "" for env in observed)


@pytest.mark.skipif(sys.platform != "linux", reason="Process descendants are read from /proc")
def test_stop_reclaims_a_shell_child_in_its_own_session(tmp_path):
    marker = tmp_path / "child.pid"
    script = (
        "import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],"
        f"start_new_session=True);pathlib.Path({str(marker)!r}).write_text(str(p.pid));time.sleep(60)"
    )
    process = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
    worker = WorkerProcess(process, tmp_path / "unused.log", {})
    try:
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(0.02)
        assert marker.exists()
        child = marker.read_text()
        worker.stop(timeout=1)
        assert process.poll() is not None
        for _ in range(100):
            stat = Path(f"/proc/{child}/stat")
            if not stat.exists() or stat.read_text().rsplit(") ", 1)[1].split()[0] == "Z":
                break
            time.sleep(0.02)
        else:
            pytest.fail("Shell child survived its Worker lease")
    finally:
        if process.poll() is None:
            worker.stop(timeout=1)
