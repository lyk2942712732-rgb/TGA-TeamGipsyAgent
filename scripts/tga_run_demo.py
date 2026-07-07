from __future__ import annotations

import argparse

from tga.cli.main import main as cli_main


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-root", default="runs")
    parser.add_argument("--report-out", default=None)
    args = parser.parse_args()
    argv = ["run", args.config, "--run-root", args.run_root]
    if args.report_out:
        argv.extend(["--report-out", args.report_out])
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

