"""TGA CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from tga.cli.config_loader import load_task_config
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.orchestrator.run_loop import run_task
from tga.reporting.markdown_report import render_markdown_report
from tga.workers.subprocess_worker import SubprocessWorker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tga")
    parser.add_argument("config", help="Path to task.json")
    parser.add_argument("--run-root", default="runs")
    args = parser.parse_args(argv)

    task = load_task_config(args.config)
    task_root = Path(args.run_root) / task.id
    artifact_store = ArtifactStore(task_root / "artifacts")
    store = EvidenceStore(task_root / "evidence.db")
    worker = SubprocessWorker(artifact_store=artifact_store)
    run_task(task=task, store=store, worker=worker, run_root=args.run_root)

    report = render_markdown_report(store.task_snapshot(task.id))
    report_path = task_root / "reports" / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

