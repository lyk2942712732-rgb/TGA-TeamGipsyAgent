"""Claude Code CLI Worker entry point."""

import asyncio

from .cli_adapter import ClaudeAdapter
from .runtime import WorkerSession


def main() -> None:
    asyncio.run(WorkerSession(ClaudeAdapter()).run())


if __name__ == "__main__":
    main()
