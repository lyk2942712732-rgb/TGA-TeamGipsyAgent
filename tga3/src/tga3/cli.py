"""Operational entry point for the long-lived Ubuntu control service."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn

from .app import create_app
from .config import TGA3Config
from .docker_runtime import DockerContainerRuntime
from .storage import PostgresStorage


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="tga3")
    result.add_argument("--config-dir", default=str(Path(__file__).parents[2] / "config"))
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="Apply schema.sql to an empty PostgreSQL database")
    sub.add_parser("serve", help="Run the long-lived control plane")
    return result


async def _init_db(config: TGA3Config) -> None:
    storage = await PostgresStorage.connect(config.runtime.postgres_dsn)
    try:
        await storage.initialize(config.project_root / "schema.sql")
    finally:
        await storage.close()


async def _serve(config: TGA3Config) -> None:
    storage = await PostgresStorage.connect(config.runtime.postgres_dsn)
    try:
        app = create_app(config, storage, DockerContainerRuntime(config))
        server = uvicorn.Server(uvicorn.Config(app, host=config.runtime.listen_host, port=config.runtime.listen_port))
        await server.serve()
    finally:
        await storage.close()


def main() -> None:
    args = parser().parse_args()
    config = TGA3Config(args.config_dir)
    if args.command == "init-db":
        asyncio.run(_init_db(config))
    else:
        asyncio.run(_serve(config))


if __name__ == "__main__":
    main()
