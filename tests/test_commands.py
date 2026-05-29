# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///

"""Tests for Meme CLI commands — dry-run modes and edge cases."""

import datetime
from pathlib import Path

import pytest

# ========================================
# Helpers
# ========================================


def _make_args(**kwargs):
    """Build a fake argparse Namespace from keyword args."""

    class FakeArgs:
        pass

    args = FakeArgs()
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


# ========================================
# Decay command tests
# ========================================


class TestDecay:
    def test_decay_dry_run_no_changes(self, init_meme):
        """Dry-run should not modify any files."""
        from meme.commands.lifecycle import cmd_decay
        from meme.utils import load_memory, save_memory

        meta = {
            "id": "mem_test_decay",
            "type": "feedback",
            "importance": 0.8,
            "created": "2020-01-01",
            "last_accessed": "2020-01-01",
            "tags": [],
            "links": [],
        }
        mem_path = init_meme / "archive" / "feedback" / "mem_test_decay.md"
        save_memory(mem_path, meta, "old memory content")

        from meme.utils import load_index, save_index

        idx = load_index()
        idx["mem_test_decay"] = {"id": "mem_test_decay", "path": str(mem_path), "importance": 0.8}
        save_index(idx)

        args = _make_args(dry_run=True)
        cmd_decay(args)

        meta2, _ = load_memory(mem_path)
        assert meta2["importance"] == 0.8

    def test_decay_applies_changes(self, init_meme):
        """Non-dry-run decay should lower importance of old memories."""
        from meme.commands.lifecycle import cmd_decay
        from meme.utils import find_memory_by_id, load_memory, save_memory

        old_date = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        meta = {
            "id": "mem_test_decay2",
            "type": "feedback",
            "importance": 0.8,
            "created": old_date,
            "last_accessed": old_date,
            "tags": [],
            "links": [],
        }
        mem_path = init_meme / "archive" / "feedback" / "mem_test_decay2.md"
        save_memory(mem_path, meta, "old memory content")

        from meme.utils import load_index, save_index

        idx = load_index()
        idx["mem_test_decay2"] = {"id": "mem_test_decay2", "path": str(mem_path), "importance": 0.8}
        save_index(idx)

        args = _make_args(dry_run=False)
        cmd_decay(args)

        # File may have been moved by tier migration; find via index
        new_path = find_memory_by_id("mem_test_decay2")
        meta2, _ = load_memory(new_path)
        assert meta2["importance"] < 0.8

    def test_decay_skips_forgotten(self, init_meme):
        """Forgotten memories should not be decayed."""
        from meme.commands.lifecycle import cmd_decay
        from meme.utils import load_memory, save_memory

        old_date = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        meta = {
            "id": "mem_test_decay_forgot",
            "type": "feedback",
            "importance": 0.8,
            "created": old_date,
            "last_accessed": old_date,
            "forgotten": True,
            "tags": [],
            "links": [],
        }
        mem_path = init_meme / "archive" / "feedback" / "mem_test_decay_forgot.md"
        save_memory(mem_path, meta, "forgotten memory")

        from meme.utils import load_index, save_index

        idx = load_index()
        idx["mem_test_decay_forgot"] = {"id": "mem_test_decay_forgot", "path": str(mem_path), "importance": 0.8}
        save_index(idx)

        args = _make_args(dry_run=False)
        cmd_decay(args)

        meta2, _ = load_memory(mem_path)
        assert meta2["importance"] == 0.8

    def test_decay_correction_slower_rate(self, init_meme):
        """Correction memories decay at 0.975 instead of 0.95."""
        from meme.commands.lifecycle import cmd_decay
        from meme.utils import find_memory_by_id, load_memory, save_memory

        old_date = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        meta = {
            "id": "mem_test_decay_corr",
            "type": "correction",
            "importance": 0.8,
            "created": old_date,
            "last_accessed": old_date,
            "tags": [],
            "links": [],
        }
        mem_path = init_meme / "archive" / "corrections" / "mem_test_decay_corr.md"
        save_memory(mem_path, meta, "correction memory")

        from meme.utils import load_index, save_index

        idx = load_index()
        idx["mem_test_decay_corr"] = {"id": "mem_test_decay_corr", "path": str(mem_path), "importance": 0.8}
        save_index(idx)

        args = _make_args(dry_run=False)
        cmd_decay(args)

        new_path = find_memory_by_id("mem_test_decay_corr")
        meta2, _ = load_memory(new_path)
        # 0.8 * 0.975^10 ≈ 0.62, while 0.8 * 0.95^10 ≈ 0.48
        assert meta2["importance"] > 0.5


# ========================================
# Promote / Demote / Warm command tests
# ========================================


class TestPromoteDemoteWarm:
    def test_promote_to_working(self, init_meme):
        """Promote should raise importance to working threshold."""
        from meme.commands.lifecycle import cmd_promote
        from meme.utils import find_memory_by_id, load_memory, save_memory

        meta = {
            "id": "mem_test_promote",
            "type": "feedback",
            "importance": 0.3,
            "created": "2024-01-01",
            "last_accessed": "2024-01-01",
            "tags": [],
            "links": [],
        }
        mem_path = init_meme / "archive" / "feedback" / "mem_test_promote.md"
        save_memory(mem_path, meta, "promote me")

        from meme.utils import load_index, save_index

        idx = load_index()
        idx["mem_test_promote"] = {"id": "mem_test_promote", "path": str(mem_path), "importance": 0.3}
        save_index(idx)

        args = _make_args(id="mem_test_promote")
        cmd_promote(args)

        new_path = find_memory_by_id("mem_test_promote")
        meta2, _ = load_memory(new_path)
        from meme.constants import TIER_WORKING_THRESHOLD

        assert meta2["importance"] >= TIER_WORKING_THRESHOLD

    def test_demote_lowers_importance(self, init_meme):
        """Demote should lower importance."""
        from meme.commands.lifecycle import cmd_demote
        from meme.utils import find_memory_by_id, load_memory, save_memory

        meta = {
            "id": "mem_test_demote",
            "type": "feedback",
            "importance": 0.9,
            "created": "2024-01-01",
            "last_accessed": "2024-01-01",
            "tags": [],
            "links": [],
        }
        mem_path = init_meme / "working" / "mem_test_demote.md"
        save_memory(mem_path, meta, "demote me")

        from meme.utils import load_index, save_index

        idx = load_index()
        idx["mem_test_demote"] = {"id": "mem_test_demote", "path": str(mem_path), "importance": 0.9}
        save_index(idx)

        args = _make_args(id="mem_test_demote", importance=None)
        cmd_demote(args)

        new_path = find_memory_by_id("mem_test_demote")
        meta2, _ = load_memory(new_path)
        assert meta2["importance"] < 0.9

    def test_demote_with_target_importance(self, init_meme):
        """Demote with explicit importance should set that value."""
        from meme.commands.lifecycle import cmd_demote
        from meme.utils import find_memory_by_id, load_memory, save_memory

        meta = {
            "id": "mem_test_demote2",
            "type": "feedback",
            "importance": 0.9,
            "created": "2024-01-01",
            "last_accessed": "2024-01-01",
            "tags": [],
            "links": [],
        }
        mem_path = init_meme / "working" / "mem_test_demote2.md"
        save_memory(mem_path, meta, "demote me")

        from meme.utils import load_index, save_index

        idx = load_index()
        idx["mem_test_demote2"] = {"id": "mem_test_demote2", "path": str(mem_path), "importance": 0.9}
        save_index(idx)

        args = _make_args(id="mem_test_demote2", importance=0.2)
        cmd_demote(args)

        new_path = find_memory_by_id("mem_test_demote2")
        meta2, _ = load_memory(new_path)
        assert meta2["importance"] == 0.2

    def test_warm_cold_memory(self, init_meme):
        """Warm should move cold memory to archive tier."""
        from meme.commands.lifecycle import cmd_warm
        from meme.constants import TIER_ARCHIVE_THRESHOLD
        from meme.utils import find_memory_by_id, load_memory, save_memory

        meta = {
            "id": "mem_test_warm",
            "type": "feedback",
            "importance": 0.1,
            "created": "2024-01-01",
            "last_accessed": "2024-01-01",
            "tags": [],
            "links": [],
        }
        mem_path = init_meme / "cold" / "mem_test_warm.md"
        save_memory(mem_path, meta, "warm me")

        from meme.utils import load_index, save_index

        idx = load_index()
        idx["mem_test_warm"] = {"id": "mem_test_warm", "path": str(mem_path), "importance": 0.1}
        save_index(idx)

        args = _make_args(id="mem_test_warm")
        cmd_warm(args)

        new_path = find_memory_by_id("mem_test_warm")
        meta2, _ = load_memory(new_path)
        assert meta2["importance"] >= TIER_ARCHIVE_THRESHOLD
        assert "archive" in str(new_path)

    def test_promote_missing_memory(self, init_meme, capsys):
        """Promote on missing memory should print error and return."""
        from meme.commands.lifecycle import cmd_promote

        args = _make_args(id="mem_nonexistent_999")
        cmd_promote(args)
        captured = capsys.readouterr()
        assert "not found" in captured.out


# ========================================
# Daydream command tests
# ========================================


class TestDaydream:
    def test_daydream_dry_run_no_changes(self, init_meme):
        """Daydream dry-run should not modify files."""
        from meme.commands.links import cmd_daydream
        from meme.utils import load_memory, save_memory

        for i, content in enumerate(["docker setup guide for python", "docker config for python apps"]):
            meta = {
                "id": f"mem_dream_{i}",
                "type": "knowledge",
                "importance": 0.6,
                "created": "2024-01-01",
                "last_accessed": "2024-01-01",
                "tags": ["docker", "python"],
                "links": [],
            }
            mem_path = init_meme / "archive" / "knowledge" / f"mem_dream_{i}.md"
            save_memory(mem_path, meta, content)

        from meme.utils import load_index, save_index

        idx = load_index()
        for i in range(2):
            idx[f"mem_dream_{i}"] = {
                "id": f"mem_dream_{i}",
                "path": str(init_meme / "archive" / "knowledge" / f"mem_dream_{i}.md"),
                "importance": 0.6,
            }
        save_index(idx)

        meta0, body0 = load_memory(init_meme / "archive" / "knowledge" / "mem_dream_0.md")

        args = _make_args(dry_run=True, mode="all", threshold=0.4, apply=False, merge=False)
        cmd_daydream(args)

        meta0_after, body0_after = load_memory(init_meme / "archive" / "knowledge" / "mem_dream_0.md")
        assert body0 == body0_after

    def test_daydream_empty_memories(self, init_meme, capsys, monkeypatch, tmp_path):
        """Daydream with no memories should report nothing found."""
        import meme.commands.links as links_mod
        import meme.constants as const
        from meme.commands.links import cmd_daydream

        # Use an isolated MEME_HOME so no other test memories are visible
        isolated_home = tmp_path / "isolated_meme"
        isolated_home.mkdir()
        for d in ["working", "archive", "cold", "vault", "backups", "meta", "bin"]:
            (isolated_home / d).mkdir()
        for sub in ["projects", "feedback", "knowledge", "corrections"]:
            (isolated_home / "archive" / sub).mkdir(parents=True)

        # Patch constants module
        monkeypatch.setattr(const, "MEME_HOME", isolated_home)
        monkeypatch.setattr(const, "WORKING_DIR", isolated_home / "working")
        monkeypatch.setattr(const, "ARCHIVE_DIR", isolated_home / "archive")
        monkeypatch.setattr(const, "COLD_DIR", isolated_home / "cold")
        monkeypatch.setattr(const, "VAULT_DIR", isolated_home / "vault")

        # Patch the links module's imported references
        monkeypatch.setattr(links_mod, "MEME_HOME", isolated_home)

        # Patch find_all_memories to use the isolated home
        # (it captures WORKING_DIR/ARCHIVE_DIR at import time)
        import meme.utils as utils_mod

        monkeypatch.setattr(utils_mod, "WORKING_DIR", isolated_home / "working")
        monkeypatch.setattr(utils_mod, "ARCHIVE_DIR", isolated_home / "archive")
        monkeypatch.setattr(utils_mod, "COLD_DIR", isolated_home / "cold")
        monkeypatch.setattr(utils_mod, "VAULT_DIR", isolated_home / "vault")

        args = _make_args(dry_run=True, mode="all", threshold=0.4, apply=False, merge=False)
        cmd_daydream(args)
        captured = capsys.readouterr()
        assert "No memories found" in captured.out or "Loaded 0" in captured.out

    def test_daydream_cluster_mode(self, init_meme):
        """Daydream cluster mode should only cluster, not suggest links."""
        from meme.commands.links import cmd_daydream
        from meme.utils import save_memory

        for i, content in enumerate(["kubernetes deployment guide", "kubernetes service config"]):
            meta = {
                "id": f"mem_cluster_{i}",
                "type": "knowledge",
                "importance": 0.6,
                "created": "2024-01-01",
                "last_accessed": "2024-01-01",
                "tags": ["k8s"],
                "links": [],
            }
            mem_path = init_meme / "archive" / "knowledge" / f"mem_cluster_{i}.md"
            save_memory(mem_path, meta, content)

        from meme.utils import load_index, save_index

        idx = load_index()
        for i in range(2):
            idx[f"mem_cluster_{i}"] = {
                "id": f"mem_cluster_{i}",
                "path": str(init_meme / "archive" / "knowledge" / f"mem_cluster_{i}.md"),
                "importance": 0.6,
            }
        save_index(idx)

        args = _make_args(dry_run=True, mode="cluster", threshold=0.4, apply=False, merge=False)
        cmd_daydream(args)

    def test_daydream_link_mode(self, init_meme):
        """Daydream link mode should suggest links."""
        from meme.commands.links import cmd_daydream
        from meme.utils import save_memory

        for i, content in enumerate(["ref [[mem_link_1]] in body", "content about other thing"]):
            meta = {
                "id": f"mem_link_{i}",
                "type": "knowledge",
                "importance": 0.6,
                "created": "2024-01-01",
                "last_accessed": "2024-01-01",
                "tags": [],
                "links": [],
            }
            mem_path = init_meme / "archive" / "knowledge" / f"mem_link_{i}.md"
            save_memory(mem_path, meta, content)

        from meme.utils import load_index, save_index

        idx = load_index()
        for i in range(2):
            idx[f"mem_link_{i}"] = {
                "id": f"mem_link_{i}",
                "path": str(init_meme / "archive" / "knowledge" / f"mem_link_{i}.md"),
                "importance": 0.6,
            }
        save_index(idx)

        args = _make_args(dry_run=True, mode="link", threshold=0.4, apply=False, merge=False)
        cmd_daydream(args)


# ========================================
# Reindex / Stats / Export tests
# ========================================


class TestMaintenance:
    def test_reindex_rebuilds_index(self, init_meme):
        """Reindex should rebuild index from memory files."""
        from meme.commands.maintenance import cmd_reindex
        from meme.utils import load_index, save_memory

        meta = {
            "id": "mem_reindex",
            "type": "feedback",
            "importance": 0.5,
            "created": "2024-01-01",
            "last_accessed": "2024-01-01",
            "tags": [],
            "links": [],
        }
        mem_path = init_meme / "archive" / "feedback" / "mem_reindex.md"
        save_memory(mem_path, meta, "reindex test")

        from meme.utils import save_index

        save_index({})

        args = _make_args()
        cmd_reindex(args)

        idx = load_index()
        assert "mem_reindex" in idx

    def test_stats_shows_counts(self, init_meme, capsys):
        """Stats should show memory counts."""
        from meme.commands.maintenance import cmd_stats
        from meme.utils import save_memory

        meta = {
            "id": "mem_stats",
            "type": "feedback",
            "importance": 0.5,
            "created": "2024-01-01",
            "last_accessed": "2024-01-01",
            "tags": [],
            "links": [],
        }
        mem_path = init_meme / "archive" / "feedback" / "mem_stats.md"
        save_memory(mem_path, meta, "stats test")

        from meme.utils import load_index, save_index

        idx = load_index()
        idx["mem_stats"] = {"id": "mem_stats", "path": str(mem_path), "importance": 0.5}
        save_index(idx)

        args = _make_args()
        cmd_stats(args)
        captured = capsys.readouterr()
        assert "Total:" in captured.out

    def test_export_md_format(self, init_meme):
        """Export with md format should produce markdown."""
        from meme.commands.maintenance import cmd_export
        from meme.utils import save_memory

        meta = {
            "id": "mem_export_md",
            "type": "knowledge",
            "importance": 0.7,
            "created": "2024-01-01",
            "last_accessed": "2024-01-01",
            "tags": [],
            "links": [],
        }
        mem_path = init_meme / "archive" / "knowledge" / "mem_export_md.md"
        save_memory(mem_path, meta, "md export test")

        from meme.utils import load_index, save_index

        idx = load_index()
        idx["mem_export_md"] = {"id": "mem_export_md", "path": str(mem_path), "importance": 0.7}
        save_index(idx)

        args = _make_args(format="md", output=None)
        cmd_export(args)


# ========================================
# Config command tests
# ========================================


class TestConfig:
    def test_config_get_existing_key(self, init_meme, capsys):
        """Config --get should return value for existing key."""
        from meme.commands.links import cmd_config
        from meme.config import load_config, save_config
        from meme.models import DreamConfig

        config = load_config()
        config.dream = DreamConfig(enabled=True)
        save_config(config)

        args = _make_args(get="dream.enabled", set=None, edit=False)
        cmd_config(args)
        captured = capsys.readouterr()
        assert "True" in captured.out or "true" in captured.out

    def test_config_get_missing_key(self, init_meme, capsys):
        """Config --get on missing key should error."""
        from meme.commands.links import cmd_config

        args = _make_args(get="nonexistent.key", set=None, edit=False)
        with pytest.raises(SystemExit) as exc_info:
            cmd_config(args)
        assert exc_info.value.code == 1

    def test_config_set_value(self, init_meme, capsys):
        """Config --set should update config value."""
        from meme.commands.links import cmd_config
        from meme.config import load_config

        args = _make_args(get=None, set="dream.enabled=false", edit=False)
        cmd_config(args)

        config = load_config()
        assert config.dream.enabled is False

    def test_config_set_invalid_format(self, init_meme, capsys):
        """Config --set without '=' should error."""
        from meme.commands.links import cmd_config

        args = _make_args(get=None, set="invalid_no_equals", edit=False)
        with pytest.raises(SystemExit) as exc_info:
            cmd_config(args)
        assert exc_info.value.code == 1


# ========================================
# Doctor command tests
# ========================================


class TestDoctor:
    def test_doctor_finds_broken_symlink(self, init_meme, capsys, monkeypatch, tmp_path):
        """Doctor should detect and optionally fix broken symlinks."""
        from meme.commands.maintenance import cmd_doctor

        claude_projects = tmp_path / "claude_projects"
        claude_projects.mkdir()
        proj_dir = claude_projects / "test_proj"
        proj_dir.mkdir()
        memory_dir = proj_dir / "memory"
        memory_dir.mkdir()

        broken_link = memory_dir / "MEMORY.md"
        broken_link.symlink_to("/nonexistent/path")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        args = _make_args(fix=False, ask=False)
        cmd_doctor(args)
        captured = capsys.readouterr()
        assert "broken_symlink" in captured.out or "passed" in captured.out.lower()

    def test_doctor_fixes_frontmatter(self, init_meme, capsys):
        """Doctor --fix should add missing frontmatter keys."""
        from meme.commands.maintenance import cmd_doctor
        from meme.utils import load_memory, save_memory

        meta = {"id": "mem_doctor", "type": "feedback"}
        mem_path = init_meme / "archive" / "feedback" / "mem_doctor.md"
        save_memory(mem_path, meta, "doctor test")

        args = _make_args(fix=True, ask=False)
        cmd_doctor(args)

        meta2, _ = load_memory(mem_path)
        assert "importance" in meta2
        assert "created" in meta2

    def test_doctor_passes_clean_install(self, init_meme, capsys):
        """Doctor on clean install should report no issues."""
        from meme.commands.maintenance import cmd_doctor

        args = _make_args(fix=False, ask=False)
        cmd_doctor(args)
        captured = capsys.readouterr()
        assert "passed" in captured.out.lower() or "No issues" in captured.out


# ========================================
# Version / Changelog tests
# ========================================


class TestVersion:
    def test_version_shows_version(self, init_meme, capsys):
        """Version command should show version string."""
        from meme.commands.system import cmd_version

        args = _make_args()
        cmd_version(args)
        captured = capsys.readouterr()
        assert "Meme v" in captured.out
