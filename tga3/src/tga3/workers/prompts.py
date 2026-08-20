"""Worker system prompts are injected from config/agents.json."""

import os


def worker_instructions() -> str:
    return os.environ["TGA3_SYSTEM_PROMPT"]


__all__ = ["worker_instructions"]
