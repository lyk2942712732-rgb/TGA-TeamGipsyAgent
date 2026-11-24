"""Qwen Code CLI entry point (supports worker, scout, and reporter roles)."""

from .adapter_factory import run_role_entrypoint


def main() -> None:
    run_role_entrypoint("qwen_cli")


if __name__ == "__main__":
    main()
