"""Root CLI entrypoint (`python forge.py ...`)."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = str(Path(__file__).resolve().parent / "src")
if not sys.path or sys.path[0] != _SRC_DIR:
    sys.path[:] = [_SRC_DIR, *[p for p in sys.path if p != _SRC_DIR]]

from forge.cli import app  # noqa: E402

if __name__ == "__main__":
    app()
