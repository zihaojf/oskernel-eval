#!/usr/bin/env python3
"""Entry point: ``python3 starry_eval.py run --arch rv``."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this file directly from the tool directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from starry_eval.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
