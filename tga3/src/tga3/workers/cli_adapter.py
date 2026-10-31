"""Headless CLI integration; conversation storage belongs to each CLI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import time
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

from .prompts import worker_instructions
from .runtime import AgentAdapter, Emit


class CliError(RuntimeError):
    pass


class CliAdapter(AgentAdapter):
    runtime = ""
    binary = ""

    def __init__(self) -> None:
        self.workspace = Path(os.environ.get("TGA3_WORKSPACE", "/workspace"))
        self.state_dir = self.workspace / ".tga3"
        self.scout_phase = os.environ.get("TGA3_SCOUT_PHASE", "executing")
        if os.environ.get("TGA3_AGENT_ROLE") == "scout":
            self.state_dir /= self.scout_phase
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.pointer = self.state_dir / f"{self.runtime}-session.json"
        self.session_id: str | None = None
        if self.pointer.is_file():
            self.session_id = json.loads(self.pointer.read_text())["session_id"]
        self.model_name = os.environ["TGA3_MODEL_NAME"]
        self.api_key = os.environ["TGA3_API_KEY"]
        self.base_url = os.environ.get("TGA3_BASE_URL") or ""
        self.timeout = int(os.environ.get("TGA3_CLI_TIMEOUT_SECONDS", "1800"))
        self.reporter = os.environ.get("TGA3_AGENT_ROLE") == "reporter"
        self.protocol = os.environ["TGA3_PROVIDER_PROTOCOL"]
        self.pending_tools: dict[str, str] = {}
        self.final_output = ""
        self.failure = ""
        self.completed = False
        self.tool_progress = False
        self.output_history = deque(maxlen=8)
        self.output_history_since = time.monotonic()
        self.stderr: deque[str] = deque(maxlen=80)

    def hook_config(self) -> dict:
        if os.environ.get("TGA3_AGENT_ROLE") not in {"scout", "worker"}:
            return {}
        command = "python -m tga3.workers.blackboard_hook"
        return {"hooks": {event: [{"hooks": [{"type": "command", "command": command, "timeout": 15}]}]
                          for event in ("UserPromptSubmit", "PreToolUse")}}

    def remember_session(self, session_id: str) -> None:
        self.session_id = session_id
        temporary = self.pointer.with_suffix(".tmp")
        temporary.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
        temporary.replace(self.pointer)

    async def set_model(self, params: dict[str, Any]) -> None:
        expected = "openai_responses" if self.runtime == "codex_cli" else "anthropic"
        if params["protocol"] != expected:
            raise ValueError(f"{self.runtime} requires {expected}")
        self.model_name = str(params["model_name"])
        self.api_key = str(params["api_key"])
        self.base_url = str(params.get("base_url") or "")
        os.environ["TGA3_MODEL_NAME"] = self.model_name
        os.environ["TGA3_API_KEY"] = self.api_key
        os.environ["TGA3_BASE_URL"] = self.base_url

    def command(self) -> tuple[list[str], dict[str, str]]:
        raise NotImplementedError

    async def event(self, value: dict[str, Any], emit: Emit) -> None:
        raise NotImplementedError

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        # CLI shell/PTY tools may create their own sessions. Capture descendants
        # before terminating the CLI, so detached tool groups are cleaned too.
        descendants: set[int] = set()
        if Path("/proc").is_dir():
            parents: dict[int, int] = {}
            for path in Path("/proc").glob("[0-9]*/stat"):
                try:
                    fields = path.read_text().rsplit(") ", 1)[1].split()
                    parents[int(path.parent.name)] = int(fields[1])
                except (OSError, ValueError, IndexError):
                    continue
            frontier = {process.pid}
            while frontier:
                frontier = {pid for pid, parent in parents.items() if parent in frontier} - descendants
                descendants.update(frontier)
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif process.returncode is None:
            process.terminate()
        for pid in descendants:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            pass
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.returncode is None:
            process.kill()
        for pid in descendants:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await process.wait()
        for _ in range(100):
            live = False
            for pid in descendants:
                try:
                    state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]
                    live |= state != "Z"
                except (OSError, IndexError):
                    pass
            if not live:
                return
            await asyncio.sleep(0.05)
        raise CliError("CLI tool processes did not stop")

    async def run_cycle(self, prompt: str, emit: Emit) -> str:
        attempt = 0
        while True:
            try:
                output = await self._run_once(prompt, emit)
                if self.tool_progress:
                    self.output_history.clear()
                else:
                    self.output_history.append(output)
                if len(self.output_history) < 8 or len(set(self.output_history)) != 1:
                    self.output_history_since = time.monotonic()
                elif time.monotonic() - self.output_history_since >= 30:
                    raise CliError("Repeated identical CLI replies without progress")
                return output
            except CliError as exc:
                if not self.session_id:
                    raise
                attempt += 1
                await emit("agent.recovery", {
                    "summary": "CLI 异常，保留上下文并恢复原会话", "attempt": attempt,
                    "cli_session_id": self.session_id, "reason": str(exc)[-2000:],
                })
                await asyncio.sleep(min(30, 2 ** min(attempt, 5)))
                prompt = (
                    "Continue the original task in this same session. The prior CLI invocation failed, stalled "
                    "or repeated identical tool results. Review its existing context and saved files, "
                    "change the failing approach rather than repeating it, and continue.\n" + prompt
                )[-16000:]

    async def _run_once(self, prompt: str, emit: Emit) -> str:
        self.final_output = self.failure = ""
        self.completed = False
        self.tool_progress = False
        self.stderr.clear()
        self.pending_tools.clear()
        last_activity = time.monotonic()
        repeated = deque(maxlen=8)
        repeat_since = last_activity
        original_emit = emit

        async def tracked_emit(method, params):
            nonlocal repeat_since
            if method == "agent.action.completed":
                self.tool_progress = True
                fingerprint = params.get("result_fingerprint")
                if fingerprint:
                    repeated.append(fingerprint)
                    if len(repeated) < repeated.maxlen or len(set(repeated)) != 1:
                        repeat_since = time.monotonic()
                    elif time.monotonic() - repeat_since >= 30:
                        raise CliError("Repeated identical tool/result loop detected")
            await original_emit(method, params)

        emit = tracked_emit
        command, env = self.command()
        env["TGA3_CLI_GENERATION"] = str(uuid4())
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.workspace,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=8 * 1024 * 1024,
            start_new_session=os.name == "posix",
        )

        async def read_stdout() -> None:
            nonlocal last_activity
            assert process.stdout is not None
            async for line in process.stdout:
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise CliError("CLI emitted invalid JSON; check the pinned CLI version") from exc
                if not isinstance(value, dict):
                    raise CliError("CLI event must be a JSON object")
                if not value.get("error") and not value.get("is_error") and not any(
                    word in str(value.get("subtype", "")) for word in ("error", "retry")
                ):
                    last_activity = time.monotonic()
                await self.event(value, emit)

        async def read_stderr() -> None:
            assert process.stderr is not None
            async for line in process.stderr:
                self.stderr.append(line.decode(errors="replace").rstrip()[-4000:])

        async def exchange() -> None:
            assert process.stdin is not None
            process.stdin.write(prompt.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            await asyncio.gather(read_stdout(), read_stderr())
            await process.wait()

        async def watchdog():
            idle_limit = min(float(os.environ.get("TGA3_CLI_IDLE_TIMEOUT_SECONDS", "300")), self.timeout / 3)
            while True:
                await asyncio.sleep(min(5, idle_limit / 2))
                if time.monotonic() - last_activity >= idle_limit:
                    raise CliError("CLI produced no progress before the inactivity deadline")

        exchange_task = asyncio.create_task(exchange())
        watchdog_task = asyncio.create_task(watchdog())
        try:
            async with asyncio.timeout(self.timeout):
                done, _ = await asyncio.wait({exchange_task, watchdog_task}, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    await task
        except TimeoutError as exc:
            raise CliError(f"CLI cycle timed out after {self.timeout}s") from exc
        finally:
            exchange_task.cancel()
            watchdog_task.cancel()
            await asyncio.gather(exchange_task, watchdog_task, return_exceptions=True)
            await self._terminate(process)
        if process.returncode or self.failure or not self.completed:
            detail = "\n".join(self.stderr)
            detail = detail.replace(self.api_key, "[redacted]") if self.api_key else detail
            failure = self.failure.replace(self.api_key, "[redacted]") if self.api_key else self.failure
            raise CliError(f"{self.runtime} failed (exit={process.returncode}): {failure}\n{detail}")
        if os.environ.get("TGA3_AGENT_ROLE") in {"scout", "worker"}:
            from .blackboard_hook import checkpoint
            # The completed model turn confirms it consumed the last injected context.
            # Acknowledgement must never fetch fresh content outside a tool opportunity.
            original_generation = os.environ.get("TGA3_CLI_GENERATION")
            os.environ["TGA3_CLI_GENERATION"] = env["TGA3_CLI_GENERATION"]
            try:
                await asyncio.to_thread(checkpoint, {"ack_only": True})
            finally:
                if original_generation is None:
                    os.environ.pop("TGA3_CLI_GENERATION", None)
                else:
                    os.environ["TGA3_CLI_GENERATION"] = original_generation
        if self.session_id:
            await emit("agent.session", {"cli_session_id": self.session_id, "runtime": self.runtime})
        return self.final_output


class CodexAdapter(CliAdapter):
    runtime = "codex_cli"
    binary = "codex"

    def command(self) -> tuple[list[str], dict[str, str]]:
        if self.protocol != "openai_responses":
            raise ValueError("Codex CLI requires the Responses protocol")
        cli_home = self.workspace / (".codex-extract" if os.environ.get("TGA3_AGENT_ROLE") == "scout"
                                     and self.scout_phase == "extracting" else ".codex")
        cli_home.mkdir(exist_ok=True)
        instructions = (
            os.environ["TGA3_SYSTEM_PROMPT"]
            + "\nRead /workspace/report-input.json and read-only evidence in /artifacts. "
            "Skills are in /skills; read SKILL.md and referenced files directly. "
            "Evidence is grouped by Worker under /artifacts/<worker-id>/. Do not contact targets or change evidence. "
            "Use only recorded commands/code for reproduction steps; if absent, state they were not recorded. "
            "Never invent example commands. Return only Markdown."
            if self.reporter
            else worker_instructions()
        )
        # JSON strings are valid TOML basic strings; credentials stay in the environment.
        config = [
            f"model = {json.dumps(self.model_name)}",
            'sandbox_mode = "danger-full-access"',
            'approval_policy = "never"',
            f"developer_instructions = {json.dumps(instructions)}",
        ]
        if self.base_url:
            config += [
                'model_provider = "tga3"',
                "[model_providers.tga3]",
                'name = "TGA3"',
                f"base_url = {json.dumps(self.base_url)}",
                'env_key = "CODEX_API_KEY"',
                'wire_api = "responses"',
            ]
        if not self.reporter and os.environ.get("TGA3_AGENT_ROLE") != "scout":
            config += [
                "[mcp_servers.blackboard]",
                f"url = {json.dumps(os.environ['TGA3_BLACKBOARD_MCP_URL'])}",
                "required = true",
            ]
        elif not self.reporter and self.scout_phase == "extracting":
            config += ["[mcp_servers.intel]",
                       f"url = {json.dumps(os.environ['TGA3_SCOUT_INTEL_MCP_URL'])}", "required = true"]
        if os.environ.get("TGA3_AGENT_ROLE") in {"scout", "worker"}:
            config += ['disabled_tools = ["blackboard_sync"]']
        if self.hook_config():
            (cli_home / "hooks.json").write_text(json.dumps(self.hook_config()), encoding="utf-8")
        (cli_home / "config.toml").write_text("\n".join(config) + "\n", encoding="utf-8")
        env = dict(os.environ, CODEX_HOME=str(cli_home), CODEX_API_KEY=self.api_key)
        args = [self.binary, "--dangerously-bypass-hook-trust", "exec"] if self.hook_config() else [self.binary, "exec"]
        if self.session_id:
            args += ["resume", self.session_id]
        args += ["--json", "--skip-git-repo-check", "--model", self.model_name, "-"]
        return args, env

    async def event(self, value: dict[str, Any], emit: Emit) -> None:
        kind = value.get("type")
        if kind == "thread.started":
            self.remember_session(value["thread_id"])
            await emit("agent.session", {"cli_session_id": self.session_id, "runtime": self.runtime})
        elif kind == "turn.completed":
            self.completed = True
        elif kind == "turn.failed":
            self.failure = str(value.get("error", value))
        elif kind in {"item.started", "item.completed"}:
            item = value.get("item", {})
            item_type = item.get("type")
            if (kind == "item.completed" and item_type == "command_execution"
                    and os.environ.get("TGA3_AGENT_ROLE") == "scout" and self.scout_phase == "executing"):
                evidence = Path(os.environ.get("TGA3_ARTIFACTS", "/artifacts"))
                evidence.mkdir(parents=True, exist_ok=True)
                with (evidence / "execution.jsonl").open("a", encoding="utf-8") as output:
                    output.write(json.dumps({key: item.get(key) for key in
                                             ("id", "command", "aggregated_output", "exit_code", "status")},
                                            ensure_ascii=False) + "\n")
            if item_type == "agent_message" and kind == "item.completed":
                self.final_output = str(item.get("text", ""))
            elif item_type in {"command_execution", "mcp_tool_call", "file_change", "web_search"}:
                await emit(
                    "agent.action.started" if kind == "item.started" else "agent.action.completed",
                    {
                        "tool": item_type,
                        "tool_use_id": item.get("id"),
                        "summary": str(item.get("command") or item.get("tool") or item_type)[:500],
                        "exit_code": item.get("exit_code"),
                        "status": item.get("status"),
                        "result_fingerprint": hashlib.sha256(json.dumps([
                            item.get("command"), item.get("tool"), item.get("arguments"), item.get("aggregated_output"),
                            item.get("result"), item.get("error"),
                            item.get("exit_code"), item.get("status"),
                        ], sort_keys=True).encode()).hexdigest() if kind == "item.completed" else None,
                    },
                )


class ClaudeAdapter(CliAdapter):
    runtime = "claude_cli"
    binary = "claude"

    def command(self) -> tuple[list[str], dict[str, str]]:
        if self.protocol != "anthropic":
            raise ValueError("Claude Code CLI requires the Anthropic protocol")
        cli_home = self.workspace / ".claude"
        cli_home.mkdir(exist_ok=True)
        mcp_file = self.state_dir / "mcp.json"
        mcp_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "blackboard": {"type": "http", "url": os.environ["TGA3_BLACKBOARD_MCP_URL"]},
                    }
                }
            ),
            encoding="utf-8",
        )
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(cli_home), ANTHROPIC_API_KEY=self.api_key, DISABLE_AUTOUPDATER="1")
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        env.pop("ANTHROPIC_BASE_URL", None)
        if self.base_url:
            env["ANTHROPIC_BASE_URL"] = self.base_url
        args = [
            self.binary,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--model",
            self.model_name,
            "--system-prompt",
            worker_instructions(),
            "--tools",
            "Bash,Read,Write,Edit,Glob,Grep",
            "--allowedTools",
            "Bash,Read,Write,Edit,Glob,Grep,mcp__blackboard__*",
            "--disallowedTools",
            "AskUserQuestion,mcp__blackboard__blackboard_sync",
            "--strict-mcp-config",
            "--mcp-config",
            str(mcp_file),
            "--settings",
            json.dumps({"sandbox": {"enabled": False}, **self.hook_config()}),
        ]
        if self.session_id:
            args += ["--resume", self.session_id]
        return args, env

    async def event(self, value: dict[str, Any], emit: Emit) -> None:
        if value.get("session_id"):
            self.remember_session(value["session_id"])
        kind = value.get("type")
        if kind == "system" and value.get("subtype") == "init":
            await emit("agent.session", {"cli_session_id": self.session_id, "runtime": self.runtime})
        if kind in {"assistant", "user"}:
            for block in value.get("message", {}).get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    self.pending_tools[block["id"]] = block["name"]
                    await emit(
                        "agent.action.started",
                        {
                            "summary": block["name"],
                            "tool": block["name"],
                            "tool_use_id": block["id"],
                            "input": block.get("input", {}),
                        },
                    )
                elif block.get("type") == "tool_result":
                    tool = self.pending_tools.pop(block["tool_use_id"], "Claude tool")
                    await emit(
                        "agent.action.completed",
                        {
                            "summary": tool,
                            "tool": tool,
                            "tool_use_id": block["tool_use_id"],
                            "error": block.get("is_error", False),
                            "result_fingerprint": hashlib.sha256(json.dumps([
                                tool, block.get("content"), block.get("is_error", False),
                            ], sort_keys=True).encode()).hexdigest(),
                        },
                    )
        elif kind == "result":
            self.completed = True
            if value.get("subtype") == "error_max_turns":
                self.final_output = "本轮已达到工具调用上限；CLI 会话已保留，将在下一周期继续。"
            elif value.get("is_error"):
                self.failure = str(value.get("result") or value.get("errors") or value.get("subtype"))
            else:
                self.final_output = str(value.get("result") or "")
