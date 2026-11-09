import asyncio

from .extra_cli import OpenCodeAdapter
from .runtime import WorkerSession


def main():
    asyncio.run(WorkerSession(OpenCodeAdapter()).run())


if __name__ == "__main__":
    main()
