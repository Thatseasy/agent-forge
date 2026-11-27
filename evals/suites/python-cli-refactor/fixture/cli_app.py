"""Baseline Python CLI module used as the python-cli-refactor benchmark fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def normalize_records(items: list[str]) -> list[str]:
    """Normalize raw skill/item names into sorted unique lowercase slugs."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in items:
        cleaned = raw.strip().lower().replace(" ", "-")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return sorted(result)


def export_manifest(items: list[str], out_path: Path) -> Path:
    """Write normalized items as deterministic UTF-8 JSON without BOM and LF line endings."""
    payload = {"count": len(items), "items": normalize_records(items)}
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize CLI records cleanly.")
    parser.add_argument("input_file", type=Path, help="Input text file with one record per line.")
    parser.add_argument("--out", type=Path, required=True, help="Output manifest JSON path.")
    args = parser.parse_args(argv)
    if not args.input_file.exists():
        return 2
    lines = args.input_file.read_text(encoding="utf-8").splitlines()
    export_manifest(normalize_records(lines), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
