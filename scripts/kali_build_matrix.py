"""Read the intentionally small Kali image build matrix without PyYAML."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPO_ROOT / "containers" / "kali" / "build-matrix.yaml"


def load_matrix(path: Path = MATRIX_PATH) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_pilots = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "pilots:":
            in_pilots = True
            continue
        if not in_pilots:
            continue
        if line.startswith("- "):
            if current is not None:
                values.append(current)
            current = {}
            line = line[2:]
        if current is None or ":" not in line:
            raise ValueError(f"invalid build matrix line: {raw_line!r}")
        key, value = (item.strip() for item in line.split(":", 1))
        if key in current or not value:
            raise ValueError(f"invalid build matrix entry: {raw_line!r}")
        current[key] = value
    if current is not None:
        values.append(current)

    required = {"solver", "profile", "image", "context"}
    if not values:
        raise ValueError("build matrix has no pilot images")
    for value in values:
        if set(value) != required:
            raise ValueError(f"build matrix entry must contain exactly {sorted(required)}")
        context = (path.parent / value["context"]).resolve()
        try:
            context.relative_to(path.parent.resolve())
        except ValueError as exc:
            raise ValueError("build matrix context escapes containers/kali") from exc
        if not (context / "Dockerfile").is_file() or not (context / "toolset.json").is_file():
            raise ValueError(f"incomplete Solver image context: {value['context']}")
    for key in ("solver", "profile", "image", "context"):
        if len({value[key] for value in values}) != len(values):
            raise ValueError(f"build matrix repeats {key}")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("shell", "json"), default="shell")
    parser.add_argument("--path", type=Path, default=MATRIX_PATH)
    args = parser.parse_args()
    values = load_matrix(args.path.resolve())
    if args.format == "json":
        print(json.dumps(values, separators=(",", ":")))
    else:
        for value in values:
            print(" ".join(value[key] for key in ("solver", "profile", "image", "context")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
