"""Meme — A centralized, tiered memory system with knowledge graph."""

import sys
from pathlib import Path

try:
    import tomllib
    _pp = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    with _pp.open("rb") as f:
        __version__ = tomllib.load(f)["project"]["version"]
except Exception:
    __version__ = "0.0.0"
