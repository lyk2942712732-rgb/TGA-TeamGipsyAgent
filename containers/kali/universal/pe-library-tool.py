#!/usr/bin/python3
"""Expose useful, read-only PE summaries from the pefile and LIEF libraries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def _pefile_summary(path: Path) -> dict[str, Any]:
    import pefile as pefile_module

    pe = pefile_module.PE(str(path), fast_load=True)
    pe.parse_data_directories(
        directories=[pefile_module.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
    )
    imports = {
        entry.dll.decode(errors="replace"): [
            item.name.decode(errors="replace") if item.name else f"ordinal:{item.ordinal}"
            for item in entry.imports
        ]
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])
    }
    return {
        "entrypoint": pe.OPTIONAL_HEADER.AddressOfEntryPoint,
        "image_base": pe.OPTIONAL_HEADER.ImageBase,
        "machine": pe.FILE_HEADER.Machine,
        "sections": [section.Name.rstrip(b"\0").decode(errors="replace") for section in pe.sections],
        "imports": imports,
    }


def _lief_summary(path: Path) -> dict[str, Any]:
    import lief as lief_module

    binary = lief_module.parse(str(path))
    if binary is None:
        raise ValueError("LIEF could not parse the input")
    return {
        "format": str(binary.format),
        "entrypoint": binary.entrypoint,
        "sections": [section.name for section in binary.sections],
        "imported_libraries": list(binary.libraries),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample", type=Path, help="PE sample to parse")
    parser.add_argument("--engine", choices=("pefile", "lief"))
    args = parser.parse_args()
    engine = args.engine or Path(sys.argv[0]).name
    if not args.sample.is_file():
        parser.error(f"sample is not a file: {args.sample}")
    summary = _pefile_summary(args.sample) if engine == "pefile" else _lief_summary(args.sample)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
