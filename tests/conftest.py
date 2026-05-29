# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///

"""Pytest fixtures for Meme test suite."""

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
    # Force reimport of constants and dependent modules with new env var
    import importlib

    import meme.constants as c

    importlib.reload(c)
    # Reload modules that depend on constants
    import meme.config as cfg
    import meme.utils as utils

    importlib.reload(cfg)
    importlib.reload(utils)
    # Also reload core so its re-exports pick up new constants
    import meme.core as core_mod

    importlib.reload(core_mod)
    return meme_home


@pytest.fixture
def init_meme(temp_meme_home):
    """Initialize a fresh Meme installation (like `meme setup`)."""
    meme_home = temp_meme_home
    meme_home.mkdir(parents=True, exist_ok=True)

    # Create directory structure directly on the temp path
    working_dir = meme_home / "working"
    archive_dir = meme_home / "archive"
    cold_dir = meme_home / "cold"
    vault_dir = meme_home / "vault"
    backups_dir = meme_home / "backups"
    meta_dir = meme_home / "meta"
    bin_dir = meme_home / "bin"

    for d in [working_dir, archive_dir, cold_dir, vault_dir, backups_dir, meta_dir, bin_dir]:
        d.mkdir(parents=True, exist_ok=True)
    for sub in ["projects", "feedback", "knowledge", "corrections"]:
        (archive_dir / sub).mkdir(parents=True, exist_ok=True)

    # Write empty meta files directly
    (meta_dir / "index.json").write_text("{}", encoding="utf-8")
    (meta_dir / "graph.json").write_text("{}", encoding="utf-8")
    (meme_home / ".gitignore").write_text("vault/*.enc\n", encoding="utf-8")
    return meme_home


@pytest.fixture
def reload_modules(temp_meme_home):
    """Reload meme modules so they pick up the temp MEME_HOME."""
    import importlib

    import meme.config as cfg
    import meme.constants as c
    import meme.utils as utils

    importlib.reload(c)
    importlib.reload(cfg)
    importlib.reload(utils)
    # Ensure the temp meme home directory exists
    c.MEME_HOME.mkdir(parents=True, exist_ok=True)
    return temp_meme_home


@pytest.fixture
def cli_runner(init_meme):
    """Run meme CLI commands and return (exit_code, stdout, stderr)."""
    meme_home = str(init_meme)

    def run(*args):
        env = {**os.environ, "MEME_HOME": meme_home}
        result = subprocess.run(
            [sys.executable, "-m", "meme.core"] + list(args),
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )
        return result.returncode, result.stdout, result.stderr

    return run
