# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///

"""Tests for Meme hook scripts (bash)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_SRC = REPO_ROOT / "src" / "meme" / "hooks"


def _create_meme_bin_wrapper(meme_home: Path) -> None:
    """Create a bin/meme wrapper in meme_home so hook scripts can invoke the CLI."""
    bin_dir = meme_home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "meme"
    wrapper.write_text(
        f'#!/usr/bin/env bash\nexport MEME_HOME="{meme_home}"\nexec {sys.executable} -m meme.core "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)


class TestQueryHook:
    def test_query_hook_empty_prompt(self, tmp_path, monkeypatch):
        """Empty prompt should return suppressOutput."""
        meme_home = tmp_path / ".meme"
        monkeypatch.setenv("MEME_HOME", str(meme_home))

        result = subprocess.run(
            ["bash", str(HOOKS_SRC / "query.sh")],
            input=json.dumps({"prompt": [{"type": "text", "text": ""}]}),
            capture_output=True,
            text=True,
            env={**os.environ, "MEME_HOME": str(meme_home)},
        )
        out = json.loads(result.stdout)
        assert out["continue"] is True
        assert out.get("suppressOutput") is True

    def test_query_hook_no_match(self, tmp_path, monkeypatch):
        """Unrelated prompt should return suppressOutput."""
        meme_home = tmp_path / ".meme"
        monkeypatch.setenv("MEME_HOME", str(meme_home))

        result = subprocess.run(
            ["bash", str(HOOKS_SRC / "query.sh")],
            input=json.dumps({"prompt": [{"type": "text", "text": "今天天气怎么样"}]}),
            capture_output=True,
            text=True,
            env={**os.environ, "MEME_HOME": str(meme_home)},
        )
        out = json.loads(result.stdout)
        assert out["continue"] is True
        assert out.get("suppressOutput") is True

    def test_query_hook_match(self, tmp_path, monkeypatch):
        """Prompt matching a memory should return additionalContext."""
        meme_home = tmp_path / ".meme"
        monkeypatch.setenv("MEME_HOME", str(meme_home))
        # Initialize minimal structure
        for d in [meme_home / "archive" / "knowledge", meme_home / "meta"]:
            d.mkdir(parents=True, exist_ok=True)
        (meme_home / "meta" / "index.json").write_text("{}", encoding="utf-8")
        (meme_home / "meta" / "graph.json").write_text("{}", encoding="utf-8")
        _create_meme_bin_wrapper(meme_home)
        # Write a test memory
        mem_path = meme_home / "archive" / "knowledge" / "mem_test_uv.md"
        mem_path.write_text(
            '---\n'
            'id: mem_test_uv\n'
            'type: knowledge\n'
            'importance: 0.6\n'
            'created: "2026-05-27"\n'
            'last_accessed: "2026-05-27"\n'
            'access_count: 1\n'
            'tags: [uv, python]\n'
            'links: []\n'
            'superseded_by: null\n'
            'forgotten: false\n'
            'sensitive: false\n'
            'source_url: null\n'
            'source_file: null\n'
            '---\n'
            '使用 uv 管理 Python 依赖，替代 pip。\n',
            encoding="utf-8",
        )

        result = subprocess.run(
            ["bash", str(HOOKS_SRC / "query.sh")],
            input=json.dumps({"prompt": [{"type": "text", "text": "我应该用 uv 还是 pip"}]}),
            capture_output=True,
            text=True,
            env={**os.environ, "MEME_HOME": str(meme_home)},
        )
        out = json.loads(result.stdout)
        assert out["continue"] is True
        assert "hookSpecificOutput" in out
        assert "additionalContext" in out["hookSpecificOutput"]
        assert "mem_test_uv" in out["hookSpecificOutput"]["additionalContext"]

    def test_query_hook_json_valid(self, tmp_path, monkeypatch):
        """Hook output must always be valid JSON."""
        meme_home = tmp_path / ".meme"
        monkeypatch.setenv("MEME_HOME", str(meme_home))

        prompts = [
            {"prompt": [{"type": "text", "text": "test"}]},
            {"prompt": [{"type": "text", "text": ""}]},
            {"prompt": "raw string prompt"},
            {},
        ]
        for p in prompts:
            result = subprocess.run(
                ["bash", str(HOOKS_SRC / "query.sh")],
                input=json.dumps(p),
                capture_output=True,
                text=True,
                env={**os.environ, "MEME_HOME": str(meme_home)},
            )
            # Should parse as JSON without error
            out = json.loads(result.stdout)
            assert "continue" in out


class TestSessionStartHook:
    def test_session_start_empty(self, tmp_path, monkeypatch):
        """Session start with no working memories."""
        meme_home = tmp_path / ".meme"
        monkeypatch.setenv("MEME_HOME", str(meme_home))
        for d in [meme_home / "working", meme_home / "meta"]:
            d.mkdir(parents=True, exist_ok=True)
        (meme_home / "meta" / "session_heat.json").write_text(
            '{"session_id":"test","started":"2026-05-27T00:00:00","heat_map":{}}',
            encoding="utf-8",
        )

        result = subprocess.run(
            ["bash", str(HOOKS_SRC / "session_start.sh")],
            capture_output=True,
            text=True,
            env={**os.environ, "MEME_HOME": str(meme_home)},
        )
        out = json.loads(result.stdout)
        assert out["continue"] is True

    def test_session_start_with_working(self, tmp_path, monkeypatch):
        """Session start loads working memories."""
        meme_home = tmp_path / ".meme"
        monkeypatch.setenv("MEME_HOME", str(meme_home))
        for d in [meme_home / "working", meme_home / "meta"]:
            d.mkdir(parents=True, exist_ok=True)
        # Write a working memory
        mem_path = meme_home / "working" / "user_identity.md"
        mem_path.write_text(
            '---\n'
            'id: mem_user\n'
            'type: user\n'
            'importance: 0.9\n'
            'created: "2026-05-27"\n'
            'last_accessed: "2026-05-27"\n'
            'access_count: 1\n'
            'tags: []\n'
            'links: []\n'
            'superseded_by: null\n'
            'forgotten: false\n'
            'sensitive: false\n'
            '---\n'
            'Software engineer, Python/TypeScript\n',
            encoding="utf-8",
        )
        (meme_home / "meta" / "session_heat.json").write_text(
            '{"session_id":"test","started":"2026-05-27T00:00:00","heat_map":{}}',
            encoding="utf-8",
        )
        _create_meme_bin_wrapper(meme_home)

        result = subprocess.run(
            ["bash", str(HOOKS_SRC / "session_start.sh")],
            capture_output=True,
            text=True,
            env={**os.environ, "MEME_HOME": str(meme_home)},
        )
        out = json.loads(result.stdout)
        assert out["continue"] is True
        assert "additionalContext" in out.get("hookSpecificOutput", {})
        assert "Software engineer" in out["hookSpecificOutput"]["additionalContext"]


class TestSessionEndHook:
    def test_session_end_creates_heat(self, tmp_path, monkeypatch):
        """Session end should update session heat file."""
        meme_home = tmp_path / ".meme"
        monkeypatch.setenv("MEME_HOME", str(meme_home))
        for d in [meme_home / "meta"]:
            d.mkdir(parents=True, exist_ok=True)
        (meme_home / "meta" / "session_heat.json").write_text(
            '{"session_id":"test","started":"2026-05-27T00:00:00","heat_map":{"mem_a":{"accessed_at":"2026-05-27T01:00:00","heat":1.0}}}',
            encoding="utf-8",
        )
        _create_meme_bin_wrapper(meme_home)

        result = subprocess.run(
            ["bash", str(HOOKS_SRC / "session_end.sh")],
            capture_output=True,
            text=True,
            env={**os.environ, "MEME_HOME": str(meme_home)},
        )
        out = json.loads(result.stdout)
        assert out["continue"] is True
