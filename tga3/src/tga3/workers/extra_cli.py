"""Native OpenCode and Qwen Code clients using the shared Worker lifecycle and hooks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from ..knowledge import worker_knowledge_name, worker_knowledge_server
from .cli_adapter import CliAdapter
from .prompts import worker_instructions


def servers(adapter):
    result = {}
    if url := os.environ.get("TGA3_WEB_SEARCH_MCP_URL"):
        result["web"] = {"url": url, "headers": adapter.mcp_headers()}
    if knowledge := worker_knowledge_server():
        result[worker_knowledge_name()] = knowledge
    return result


def save_usage(adapter, ident, usage):
    if not ident or not usage:
        return
    path = adapter.state_dir / "native-usage.jsonl"
    with path.open("a", encoding="utf8") as log:
        log.write(json.dumps({"type": "native_usage", "source": f"{adapter.runtime}:{ident}", "usage": usage}) + "\n")


class QwenAdapter(CliAdapter):
    runtime, binary = "qwen_cli", "qwen"

    def command(self):
        auth = {"openai_chat_completions": "openai", "openai_responses": "openai-responses", "anthropic": "anthropic"}[
            self.protocol
        ]
        # Keep user-level hooks outside the project .qwen directory: Qwen otherwise
        # also treats them as untrusted project hooks and prints a misleading warning.
        home = self.state_dir / "qwen-home"
        home.mkdir(parents=True, exist_ok=True)
        settings = {
            "security": {"auth": {"selectedType": auth}},
            "mcpServers": {
                name: {"httpUrl": spec["url"], "headers": spec.get("headers", {}), "timeout": 30000}
                for name, spec in servers(self).items()
            },
            "tools": {"approvalMode": "yolo", "exclude": ["task", "ask_user_question"]},
            **self.hook_config(),
        }
        (home / "settings.json").write_text(json.dumps(settings), encoding="utf8")
        args = [
            self.binary,
            "-p",
            "Follow the task instructions supplied on stdin.",
            "--output-format",
            "stream-json",
            "--approval-mode",
            "yolo",
            "--auth-type",
            auth,
            "--model",
            self.model_name,
            "--append-system-prompt",
            worker_instructions(),
        ]
        if self.session_id:
            args += ["--resume", self.session_id]
        return args, dict(
            os.environ,
            QWEN_HOME=str(home),
            OPENAI_API_KEY=self.api_key,
            OPENAI_BASE_URL=self.base_url,
            OPENAI_MODEL=self.model_name,
            ANTHROPIC_API_KEY=self.api_key,
            ANTHROPIC_BASE_URL=self.base_url,
            ANTHROPIC_MODEL=self.model_name,
            NO_COLOR="1",
        )

    async def event(self, value, emit):
        if ident := value.get("session_id"):
            self.remember_session(ident)
        kind = value.get("type")
        message = value.get("message", {})
        if kind == "assistant" and message.get("usage"):
            usage = message["usage"]
            save_usage(
                self,
                message.get("id") or value.get("uuid"),
                {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cached_input_tokens": usage.get("cache_read_input_tokens", 0),
                },
            )
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
                    tool = self.pending_tools.pop(block["tool_use_id"], "Qwen tool")
                    await emit(
                        "agent.action.completed",
                        {
                            "summary": tool,
                            "tool": tool,
                            "tool_use_id": block["tool_use_id"],
                            "error": bool(block.get("is_error")),
                            "result_fingerprint": hashlib.sha256(
                                json.dumps(block, sort_keys=True).encode()
                            ).hexdigest(),
                        },
                    )
        elif kind == "result":
            self.completed = True
            if value.get("is_error"):
                self.failure = str(value.get("error") or value.get("errors") or value.get("result"))
            else:
                self.final_output = str(value.get("result") or "")


class OpenCodeAdapter(CliAdapter):
    runtime, binary = "opencode_cli", "opencode"

    def command(self):
        sdk = {
            "openai_chat_completions": "@ai-sdk/openai-compatible",
            "openai_responses": "@ai-sdk/openai",
            "anthropic": "@ai-sdk/anthropic",
        }[self.protocol]
        base_url = self.base_url.rstrip("/")
        if self.protocol == "anthropic" and not base_url.endswith("/v1"):
            # Claude/Qwen add /v1 themselves; the AI SDK Anthropic adapter expects it in baseURL.
            base_url += "/v1"
        root = self.workspace / ".opencode"
        root.mkdir(exist_ok=True)
        if os.environ.get("TGA3_HOSTED") == "1":
            dependencies = Path("/opt/tga3-hosted/opencode-deps")
            if not (dependencies / "node_modules/@opencode-ai/plugin/package.json").is_file():
                raise RuntimeError("Hosted image is missing its offline OpenCode plugin dependencies")
            for directory in [root, root / "config/opencode"]:
                directory.mkdir(parents=True, exist_ok=True)
                for name in ["package.json", "package-lock.json"]:
                    if not (directory / name).exists():
                        shutil.copyfile(dependencies / name, directory / name)
                if not (directory / "node_modules").exists():
                    (directory / "node_modules").symlink_to(dependencies / "node_modules", target_is_directory=True)
        prompt = root / "worker.md"
        prompt.write_text(worker_instructions(), encoding="utf8")
        config = {
            "$schema": "https://opencode.ai/config.json",
            "model": "tga/" + self.model_name,
            "small_model": "tga/" + self.model_name,
            "enabled_providers": ["tga"],
            "provider": {
                "tga": {
                    "npm": sdk,
                    "name": "TGA",
                    "options": {"baseURL": base_url, "apiKey": "{env:TGA3_API_KEY}"},
                    "models": {
                        self.model_name: {"name": self.model_name, "limit": {"context": 128000, "output": 8192}}
                    },
                }
            },
            "instructions": [str(prompt)],
            "plugin": [Path(__file__).with_name("opencode_plugin.js").resolve().as_uri()],
            "mcp": {
                name: {
                    "type": "remote",
                    "url": spec["url"],
                    "headers": spec.get("headers", {}),
                    "enabled": True,
                    "timeout": 30000,
                }
                for name, spec in servers(self).items()
            },
            "permission": {"*": "allow", "question": "deny", "task": "deny"},
            "snapshot": False,
            "autoupdate": False,
        }
        path = root / "opencode.json"
        if os.environ.get("TGA3_HOSTED") == "1":
            config["permission"].update(websearch="deny")
        path.write_text(json.dumps(config), encoding="utf8")
        args = [self.binary, "run", "--format", "json", "--model", "tga/" + self.model_name]
        if self.session_id:
            args += ["--session", self.session_id]
        env = dict(
            os.environ,
            OPENCODE_CONFIG=str(path),
            OPENCODE_DISABLE_DEFAULT_PLUGINS="true",
            OPENCODE_DISABLE_AUTOUPDATE="true",
            OPENCODE_DISABLE_LSP_DOWNLOAD="true",
            OPENCODE_DISABLE_MODELS_FETCH="true",
            XDG_DATA_HOME=str(root / "data"),
            XDG_CONFIG_HOME=str(root / "config"),
            XDG_STATE_HOME=str(root / "state"),
            XDG_CACHE_HOME=str(root / "cache"),
        )
        return args, env

    async def event(self, value, emit):
        if ident := value.get("sessionID"):
            fresh = self.session_id != ident
            self.remember_session(ident)
            if fresh:
                await emit("agent.session", {"cli_session_id": ident, "runtime": self.runtime})
        kind, part = value.get("type"), value.get("part", {})
        if kind == "step_finish" and part.get("tokens"):
            tokens = part["tokens"]
            cache = tokens.get("cache", {})
            save_usage(
                self,
                part.get("id"),
                {
                    "input_tokens": tokens.get("input", 0) + cache.get("read", 0) + cache.get("write", 0),
                    "output_tokens": tokens.get("output", 0) + tokens.get("reasoning", 0),
                    "cached_input_tokens": cache.get("read", 0),
                    "cache_write_input_tokens": cache.get("write", 0),
                },
            )
        if kind == "tool_use":
            state, tool = part.get("state", {}), part.get("tool", "OpenCode tool")
            await emit(
                "agent.action.completed",
                {
                    "summary": tool,
                    "tool": tool,
                    "tool_use_id": part.get("callID"),
                    "input": state.get("input", {}),
                    "error": state.get("status") == "error",
                    "result_fingerprint": hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest(),
                },
            )
        elif kind == "text":
            self.final_output += part.get("text", "")
            self.completed = True
        elif kind == "step_finish" and part.get("reason") == "stop":
            self.completed = True
        elif kind == "error":
            self.failure = str(value.get("error"))
