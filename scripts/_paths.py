"""Tiny helper so shell scripts can resolve paths from config/paths.yaml
without importing the full Settings machinery. Run as:

    python scripts/_paths.py alphagenome_weights
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} KEY", file=sys.stderr)
        sys.exit(2)
    paths = yaml.safe_load((ROOT / "config" / "paths.yaml").read_text())
    key = sys.argv[1]
    if key not in paths:
        print(f"unknown key: {key}", file=sys.stderr)
        sys.exit(2)
    print((ROOT / paths[key]).resolve())


if __name__ == "__main__":
    main()
