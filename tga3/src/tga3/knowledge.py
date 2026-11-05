"""Pinned Des-CTF-Knowledge checkout and native stdio MCP wiring."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

REPOSITORY = "https://github.com/Dest1ny-Sec/Des-CTF-Knowledge.git"
REVISION = "0911795c48f8c6ed66cab370f01567825f8fd2d5"
CONTAINER_ROOT = "/knowledge/des-ctf-knowledge"


def worker_knowledge_server() -> dict | None:
    root = os.environ.get("TGA3_CTF_KNOWLEDGE_ROOT")
    if os.environ.get("TGA3_AGENT_ROLE") != "worker" or not root:
        return None
    return {"command": "python3", "args": ["-m", "tga3.workers.knowledge_server", root],
            "env": {"PYTHONDONTWRITEBYTECODE": "1"}}


def install(destination: Path) -> None:
    destination = destination.resolve()
    if destination.exists():
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=destination, text=True).strip()
        if head != REVISION:
            raise RuntimeError(f"Existing knowledge checkout differs from pinned revision: {destination}")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--no-checkout", "--filter=blob:none", REPOSITORY, str(destination)], check=True)
        subprocess.run(["git", "checkout", "--detach", REVISION], cwd=destination, check=True)
    for name in ("SKILL.md", "AI-SEARCH-INDEX.md", "mcp/server.py", "techniques/techniques.json"):
        if not (destination / name).is_file():
            raise RuntimeError(f"Knowledge checkout is missing {name}")
    print(f"Des-CTF-Knowledge {REVISION}: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, nargs="?", default=Path("knowledge/des-ctf-knowledge"))
    install(parser.parse_args().destination)
