# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///

"""Pytest fixtures for Meme test suite."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


# Add src/ to path so we can import meme in tests
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


@pytest.fixture
def temp_meme_home(tmp_path, monkeypatch):
    """Create an isolated Meme home directory for a single test."""
    meme_home = tmp_path / ".meme"
    monkeypatch.setenv("MEME_HOME", str(meme_home))
    # Force reimport of core module with new env var
    import importlib

    import meme.core as core_mod

    importlib.reload(core_mod)
    return meme_home


@pytest.fixture
def init_meme(temp_meme_home):
    """Initialize a fresh Meme installation (like `meme setup`)."""
    import meme.core as core_mod

    core_mod.MEME_HOME.mkdir(parents=True, exist_ok=True)
    for d in [
        core_mod.WORKING_DIR,
        core_mod.ARCHIVE_DIR,
        core_mod.COLD_DIR,
        core_mod.VAULT_DIR,
        core_mod.BACKUPS_DIR,
        core_mod.META_DIR,
        core_mod.BIN_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)
    for sub in ["projects", "feedback", "knowledge", "corrections"]:
        (core_mod.ARCHIVE_DIR / sub).mkdir(parents=True, exist_ok=True)

    # Write empty meta files
    core_mod.save_index({})
    core_mod.save_graph({})
    (core_mod.MEME_HOME / ".gitignore").write_text("vault/*.enc\n", encoding="utf-8")
    return core_mod.MEME_HOME


@pytest.fixture
def reload_modules(temp_meme_home):
    """Reload meme modules so they pick up the temp MEME_HOME."""
    import importlib

    import meme.constants as c
    import meme.config as cfg
    import meme.utils as utils

    importlib.reload(c)
    importlib.reload(cfg)
    importlib.reload(utils)
    # Ensure the temp meme home directory exists
    c.MEME_HOME.mkdir(parents=True, exist_ok=True)
    return temp_meme_home


@pytest.fixture
def cli_runner(temp_meme_home):
    """Run meme CLI commands and return (exit_code, stdout, stderr)."""

    def run(*args):
        env = {**os.environ, "MEME_HOME": str(temp_meme_home)}
        result = subprocess.run(
            [sys.executable, "-m", "meme.core"] + list(args),
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )
        return result.returncode, result.stdout, result.stderr

    return run
