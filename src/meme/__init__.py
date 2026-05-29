"""Meme — A centralized, tiered memory system with knowledge graph."""

import tomllib
from pathlib import Path

# Prefer pyproject.toml (source checkout) so edits are picked up immediately.
# Fall back to importlib.metadata when installed as a wheel.
_pp = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
try:
    if _pp.exists():
        with _pp.open("rb") as f:
            __version__ = tomllib.load(f)["project"]["version"]
    else:
        from importlib.metadata import version

        __version__ = version("memectl")
except Exception:
    __version__ = "0.0.0"
