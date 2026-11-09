import asyncio

from .extra_cli import QwenAdapter
from .runtime import WorkerSession


def main():
    asyncio.run(WorkerSession(QwenAdapter()).run())


if __name__ == "__main__":
    main()
