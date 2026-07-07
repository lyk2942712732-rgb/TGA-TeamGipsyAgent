from __future__ import annotations

import argparse

from tga.cli.main import main as cli_main


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-root", default="runs")
    args = parser.parse_args()
    return cli_main([args.config, "--run-root", args.run_root])


if __name__ == "__main__":
    raise SystemExit(main())

