from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SERVER = Path(__file__).parents[1] / "sandboxd" / "images" / "nmap-mcp" / "server.py"
SPEC = importlib.util.spec_from_file_location("tga_nmap_mcp", SERVER)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_connect_command_is_fixed_and_dns_free() -> None:
    assert MODULE.command(
        "connect",
        "nmap_connect",
        {"target": "192.0.2.10", "ports": [443, 80]},
    ) == [
        "nmap", "-n", "-Pn", "-sT", "--max-rate", "1000",
        "-p", "80,443", "192.0.2.10",
    ]


def test_raw_mode_does_not_expose_arbitrary_command() -> None:
    with pytest.raises(ValueError):
        MODULE.command("raw", "shell", {"target": "192.0.2.10"})


@pytest.mark.parametrize(
    "value",
    ["example.com", "192.0.2.1;id", "192.0.2.1/24", "127.0.0.1\n--script"],
)
def test_target_rejects_dns_injection_and_noncanonical_values(value: str) -> None:
    with pytest.raises(ValueError):
        MODULE.target(value)
