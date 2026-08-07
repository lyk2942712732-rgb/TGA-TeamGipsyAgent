"""Read and validate the Kali Solver image build matrix without PyYAML."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPO_ROOT / "containers" / "kali" / "build-matrix.yaml"


def load_matrix(path: Path = MATRIX_PATH) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_images = False
    saw_images = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        indent = len(raw_line) - len(raw_line.lstrip())
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "images:" and indent == 0:
            if saw_images:
                raise ValueError("build matrix repeats images")
            saw_images = True
            in_images = True
            continue
        if indent == 0:
            if in_images and current is not None:
                values.append(current)
                current = None
            in_images = False
            continue
        if not in_images:
            continue
        if indent not in (2, 4):
            raise ValueError(f"invalid build matrix indentation: {raw_line!r}")
        if line.startswith("- "):
            if indent != 2:
                raise ValueError(f"invalid build matrix entry: {raw_line!r}")
            if current is not None:
                values.append(current)
            current = {}
            line = line[2:]
        elif indent != 4:
            raise ValueError(f"invalid build matrix field: {raw_line!r}")
        if current is None or ":" not in line:
            raise ValueError(f"invalid build matrix line: {raw_line!r}")
        key, value = (item.strip() for item in line.split(":", 1))
        if key in current or not value:
            raise ValueError(f"invalid build matrix entry: {raw_line!r}")
        current[key] = value
    if in_images and current is not None:
        values.append(current)

    required = {"image", "context"}
    if not values:
        raise ValueError("build matrix has no images")
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
    for key in ("image", "context"):
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
            print(" ".join(value[key] for key in ("image", "context")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
