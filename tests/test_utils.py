# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///

"""Tests for Meme utility functions."""

import datetime

import pytest

from meme.models import MemoryMeta
from meme.utils import (
    count_tokens,
    find_all_memories,
    find_memory_by_id,
    generate_id,
    get_memory_dir,
    get_tier,
    parse_frontmatter,
    parse_memory_string,
    render_frontmatter,
    save_memory_to_string,
)


class TestParseFrontmatter:
    def test_parse_basic(self):
        """parse_frontmatter extracts YAML frontmatter and body."""
        text = "---\nid: mem_001\ntype: feedback\n---\n\nBody content here."
        meta, body = parse_frontmatter(text)
        assert meta == {"id": "mem_001", "type": "feedback"}
        assert body == "Body content here."

    def test_parse_no_frontmatter(self):
        """parse_frontmatter returns empty meta when no frontmatter."""
        text = "Just body content."
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == "Just body content."

    def test_parse_empty_frontmatter(self):
        """parse_frontmatter handles empty YAML block."""
        text = "---\n---\n\nBody content."
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == "Body content."

    def test_parse_invalid_yaml(self):
        """parse_frontmatter returns empty meta on invalid YAML."""
        text = "---\nnot: valid: yaml: ::\n---\n\nBody content."
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_parse_multiline_body(self):
        """parse_frontmatter preserves multiline body."""
        text = "---\nid: mem_001\n---\n\nLine 1\nLine 2\nLine 3"
        meta, body = parse_frontmatter(text)
        assert body == "Line 1\nLine 2\nLine 3"

    def test_parse_leading_whitespace_in_body(self):
        """parse_frontmatter strips leading newlines from body."""
        text = "---\nid: mem_001\n---\n\n\n\nBody content."
        meta, body = parse_frontmatter(text)
        assert body == "Body content."


class TestRenderFrontmatter:
    def test_render_basic(self):
        """render_frontmatter produces valid markdown with frontmatter."""
        meta = {"id": "mem_001", "type": "feedback", "importance": 0.5}
        body = "Body content."
        result = render_frontmatter(meta, body)
        assert result.startswith("---\n")
        assert "id: mem_001" in result
        assert "type: feedback" in result
        assert "importance: 0.5" in result
        assert result.endswith("Body content.")

    def test_render_bool(self):
        """render_frontmatter renders booleans correctly."""
        meta = {"id": "mem_001", "forgotten": True, "sensitive": False}
        result = render_frontmatter(meta, "")
        assert "forgotten: true" in result
        assert "sensitive: false" in result

    def test_render_empty_list(self):
        """render_frontmatter renders empty lists."""
        meta = {"id": "mem_001", "tags": []}
        result = render_frontmatter(meta, "")
        assert "tags: []" in result

    def test_render_non_empty_list(self):
        """render_frontmatter renders non-empty lists."""
        meta = {"id": "mem_001", "tags": ["python", "uv"]}
        result = render_frontmatter(meta, "")
        assert "tags:" in result
        assert "python" in result
        assert "uv" in result

    def test_render_null(self):
        """render_frontmatter renders null values."""
        meta = {"id": "mem_001", "superseded_by": None}
        result = render_frontmatter(meta, "")
        assert "superseded_by: null" in result

    def test_render_preserves_unknown_keys(self):
        """render_frontmatter preserves keys not in FRONTMATTER_KEYS."""
        meta = {"id": "mem_001", "custom_key": "custom_value"}
        result = render_frontmatter(meta, "")
        assert "custom_key: custom_value" in result

    def test_render_dict(self):
        """render_frontmatter renders dict values."""
        meta = {"id": "mem_001", "nested": {"a": 1, "b": 2}}
        result = render_frontmatter(meta, "")
        assert "nested:" in result
        # Dict values are rendered via yaml.dump, which may use flow style
        assert "a: 1" in result
        assert "b: 2" in result


class TestCountTokens:
    def test_count_tokens_basic(self):
        """count_tokens returns rough token estimate."""
        assert count_tokens("") == 1
        assert count_tokens("a") == 1
        assert count_tokens("abcd") == 2
        assert count_tokens("a" * 100) == 26


class TestGenerateId:
    def test_generate_id_with_slug(self):
        """generate_id formats slug correctly."""
        mem_id = generate_id("feedback", "my-slug")
        assert mem_id.startswith("mem_")
        assert "my_slug" in mem_id

    def test_generate_id_without_slug(self):
        """generate_id creates a random slug when none provided."""
        mem_id = generate_id("feedback")
        assert mem_id.startswith("mem_")
        parts = mem_id.split("_")
        assert len(parts) == 3
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 8  # random hex

    def test_generate_id_sanitizes_slug(self):
        """generate_id sanitizes special characters in slug."""
        mem_id = generate_id("feedback", "My Slug!@#")
        assert "my_slug_" in mem_id

    def test_generate_id_date_is_today(self):
        """generate_id uses today's date."""
        today = datetime.date.today().strftime("%Y%m%d")
        mem_id = generate_id("feedback", "test")
        assert f"mem_{today}_" in mem_id


class TestGetTier:
    def test_working_tier(self):
        """Importance >= 0.8 is working tier."""
        assert get_tier({"importance": 0.8}) == "working"
        assert get_tier({"importance": 1.0}) == "working"

    def test_archive_tier(self):
        """0.2 <= importance < 0.8 is archive tier."""
        assert get_tier({"importance": 0.2}) == "archive"
        assert get_tier({"importance": 0.5}) == "archive"
        assert get_tier({"importance": 0.79}) == "archive"

    def test_cold_tier(self):
        """Importance < 0.2 is cold tier."""
        assert get_tier({"importance": 0.0}) == "cold"
        assert get_tier({"importance": 0.1}) == "cold"
        assert get_tier({"importance": 0.199}) == "cold"

    def test_default_importance(self):
        """Default importance (0.5) is archive tier."""
        assert get_tier({}) == "archive"


class TestGetMemoryDir:
    def test_working_dir(self, reload_modules):
        """Working tier returns WORKING_DIR."""
        from meme.constants import WORKING_DIR

        assert get_memory_dir("feedback", "working") == WORKING_DIR

    def test_cold_dir(self, reload_modules):
        """Cold tier returns COLD_DIR."""
        from meme.constants import COLD_DIR

        assert get_memory_dir("feedback", "cold") == COLD_DIR

    def test_archive_feedback(self, reload_modules):
        """Archive tier feedback goes to archive/feedback."""
        from meme.constants import ARCHIVE_DIR

        assert get_memory_dir("feedback", "archive") == ARCHIVE_DIR / "feedback"

    def test_archive_project(self, reload_modules):
        """Archive tier project goes to archive/projects."""
        from meme.constants import ARCHIVE_DIR

        assert get_memory_dir("project", "archive") == ARCHIVE_DIR / "projects"

    def test_archive_knowledge(self, reload_modules):
        """Archive tier knowledge goes to archive/knowledge."""
        from meme.constants import ARCHIVE_DIR

        assert get_memory_dir("knowledge", "archive") == ARCHIVE_DIR / "knowledge"

    def test_archive_unknown_type(self, reload_modules):
        """Unknown type falls back to archive/feedback."""
        from meme.constants import ARCHIVE_DIR

        assert get_memory_dir("unknown", "archive") == ARCHIVE_DIR / "feedback"


class TestSaveMemoryToString:
    def test_roundtrip(self):
        """save_memory_to_string and parse_memory_string are inverses."""
        meta = {"id": "mem_001", "type": "feedback", "importance": 0.5}
        body = "This is the body."
        text = save_memory_to_string(meta, body)
        parsed_meta, parsed_body = parse_memory_string(text)
        assert parsed_meta["id"] == "mem_001"
        assert parsed_meta["type"] == "feedback"
        assert parsed_body == "This is the body."

    def test_parse_without_frontmatter(self):
        """parse_memory_string returns empty meta for plain text."""
        meta, body = parse_memory_string("Just plain text.")
        assert meta == {}
        assert body == "Just plain text."


class TestFindAllMemories:
    def test_empty_dirs(self, reload_modules):
        """find_all_memories returns empty list when no memories exist."""
        assert find_all_memories() == []

    def test_finds_working_memories(self, reload_modules):
        """find_all_memories finds .md files in working dir."""
        from meme.constants import WORKING_DIR

        WORKING_DIR.mkdir(parents=True, exist_ok=True)
        (WORKING_DIR / "test.md").write_text("---\nid: mem_test\n---\n\nbody", encoding="utf-8")
        paths = find_all_memories()
        assert len(paths) == 1
        assert paths[0].name == "test.md"

    def test_finds_archive_memories(self, reload_modules):
        """find_all_memories finds .md files in archive subdirs."""
        from meme.constants import ARCHIVE_DIR

        feedback_dir = ARCHIVE_DIR / "feedback"
        feedback_dir.mkdir(parents=True, exist_ok=True)
        (feedback_dir / "fb.md").write_text("---\nid: mem_fb\n---\n\nbody", encoding="utf-8")
        paths = find_all_memories()
        assert len(paths) == 1
        assert paths[0].name == "fb.md"

    def test_skips_cold_by_default(self, reload_modules):
        """find_all_memories excludes cold dir by default."""
        from meme.constants import COLD_DIR

        COLD_DIR.mkdir(parents=True, exist_ok=True)
        (COLD_DIR / "cold.md").write_text("---\nid: mem_cold\n---\n\nbody", encoding="utf-8")
        paths = find_all_memories()
        assert len(paths) == 0

    def test_includes_cold_when_requested(self, reload_modules):
        """find_all_memories includes cold dir when include_cold=True."""
        from meme.constants import COLD_DIR

        COLD_DIR.mkdir(parents=True, exist_ok=True)
        (COLD_DIR / "cold.md").write_text("---\nid: mem_cold\n---\n\nbody", encoding="utf-8")
        paths = find_all_memories(include_cold=True)
        assert len(paths) == 1
        assert paths[0].name == "cold.md"

    def test_skips_forgotten_by_default(self, reload_modules):
        """find_all_memories excludes forgotten memories."""
        from meme.constants import META_DIR, WORKING_DIR

        WORKING_DIR.mkdir(parents=True, exist_ok=True)
        META_DIR.mkdir(parents=True, exist_ok=True)
        (WORKING_DIR / "forgotten.md").write_text(
            "---\nid: mem_forgotten\nforgotten: true\n---\n\nbody",
            encoding="utf-8",
        )
        # Write forgotten index
        import json

        (META_DIR / "forgotten_index.json").write_text(json.dumps({"mem_forgotten": {}}), encoding="utf-8")
        paths = find_all_memories()
        assert len(paths) == 0


class TestFindMemoryById:
    def test_found_in_working(self, reload_modules):
        """find_memory_by_id locates memory in working dir."""
        from meme.constants import WORKING_DIR

        WORKING_DIR.mkdir(parents=True, exist_ok=True)
        (WORKING_DIR / "mem_target.md").write_text("---\nid: mem_target\n---\n\nbody", encoding="utf-8")
        path = find_memory_by_id("mem_target")
        assert path is not None
        assert path.name == "mem_target.md"

    def test_not_found(self, reload_modules):
        """find_memory_by_id returns None for missing memory."""
        assert find_memory_by_id("mem_nonexistent") is None

    def test_found_in_archive(self, reload_modules):
        """find_memory_by_id locates memory in archive subdir."""
        from meme.constants import ARCHIVE_DIR

        knowledge_dir = ARCHIVE_DIR / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        (knowledge_dir / "mem_know.md").write_text("---\nid: mem_know\n---\n\nbody", encoding="utf-8")
        path = find_memory_by_id("mem_know")
        assert path is not None
        assert path.name == "mem_know.md"


class TestMemoryMeta:
    def test_from_dict_basic(self):
        """MemoryMeta.from_dict builds from dict."""
        meta = MemoryMeta.from_dict({"id": "mem_001", "type": "feedback", "importance": 0.8})
        assert meta.id == "mem_001"
        assert meta.type == "feedback"
        assert meta.importance == 0.8

    def test_from_dict_ignores_unknown_keys(self):
        """MemoryMeta.from_dict ignores unknown keys."""
        meta = MemoryMeta.from_dict({"id": "mem_001", "unknown_field": "value"})
        assert meta.id == "mem_001"
        assert not hasattr(meta, "unknown_field")

    def test_from_dict_coerces_datetime(self):
        """MemoryMeta.from_dict coerces datetime objects to strings."""
        dt = datetime.datetime(2026, 5, 27, 12, 0, 0)
        meta = MemoryMeta.from_dict({"id": "mem_001", "created": dt})
        assert meta.created == "2026-05-27T12:00:00"

    def test_to_dict_skips_none(self):
        """MemoryMeta.to_dict skips None values."""
        meta = MemoryMeta(id="mem_001")
        d = meta.to_dict()
        assert "id" in d
        assert "superseded_by" not in d
        assert "forgotten_at" not in d

    def test_to_dict_preserves_empty_list(self):
        """MemoryMeta.to_dict preserves empty lists."""
        meta = MemoryMeta(id="mem_001", tags=[])
        d = meta.to_dict()
        assert d["tags"] == []

    def test_dict_like_interface(self):
        """MemoryMeta supports dict-like access."""
        meta = MemoryMeta(id="mem_001", type="feedback")
        assert meta.get("id") == "mem_001"
        assert meta["type"] == "feedback"
        assert "id" in meta
        assert "nonexistent" not in meta
        assert "id" in meta.keys()

    def test_setitem(self):
        """MemoryMeta supports item assignment."""
        meta = MemoryMeta(id="mem_001")
        meta["importance"] = 0.9
        assert meta.importance == 0.9

    def test_setitem_unknown_key_raises(self):
        """MemoryMeta raises KeyError for unknown keys."""
        meta = MemoryMeta()
        with pytest.raises(KeyError):
            meta["unknown_key"] = "value"
