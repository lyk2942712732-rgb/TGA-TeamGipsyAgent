from __future__ import annotations

import argparse
from pathlib import Path

from tga.evidence.store import EvidenceStore
from tga.reporting.markdown_report import render_markdown_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    store = EvidenceStore(args.db)
    report = render_markdown_report(store.task_snapshot(args.task_id))
    Path(args.out).write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

