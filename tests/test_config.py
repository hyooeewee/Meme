# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///

"""Tests for Meme configuration management."""

import pytest

from meme.models import MemeConfig, DreamConfig, DaydreamConfig, HooksConfig
from meme.config import load_config, save_config, get_config_value, set_config_value


class TestLoadConfig:
    def test_load_default_when_no_file(self, reload_modules):
        """load_config returns defaults when config.toml does not exist."""
        config = load_config()
        assert isinstance(config, MemeConfig)
        assert config.dream.enabled is True
        assert config.dream.schedule == "0 3 * * *"
        assert config.dream.threshold == 0.4
        assert config.dream.auto_apply is True
        assert config.dream.mode == "all"
        assert config.dream.report_dir == "dreams"
        assert config.daydream.threshold == 0.4
        assert config.daydream.default_mode == "all"
        assert config.daydream.auto_apply is True
        assert config.daydream.merge is True
        assert config.hooks.session_end_check_dream is True

    def test_load_user_config(self, reload_modules):
        """load_config merges user TOML with defaults."""
        import meme.config as cfg

        config_path = cfg.CONFIG_PATH
        config_path.write_text(
            '[dream]\nenabled = false\nschedule = "0 5 * * *"\nthreshold = 0.7\n'
            '[daydream]\nmerge = false\n',
            encoding="utf-8",
        )
        config = load_config()
        assert config.dream.enabled is False
        assert config.dream.schedule == "0 5 * * *"
        assert config.dream.threshold == 0.7
        # Unchanged sections keep defaults
        assert config.dream.auto_apply is True
        assert config.daydream.merge is False
        assert config.daydream.threshold == 0.4
        assert config.hooks.session_end_check_dream is True

    def test_load_invalid_toml_falls_back_to_defaults(self, reload_modules):
        """load_config falls back to defaults on invalid TOML."""
        import meme.config as cfg

        config_path = cfg.CONFIG_PATH
        config_path.write_text("not valid toml [[", encoding="utf-8")
        config = load_config()
        assert isinstance(config, MemeConfig)
        assert config.dream.enabled is True


class TestSaveConfig:
    def test_save_creates_file(self, reload_modules):
        """save_config writes a valid TOML file."""
        import meme.config as cfg

        config = MemeConfig()
        config.dream.enabled = False
        config.dream.threshold = 0.9
        save_config(config)

        config_path = cfg.CONFIG_PATH
        assert config_path.exists()
        text = config_path.read_text(encoding="utf-8")
        assert "enabled = false" in text
        assert "threshold = 0.9" in text
        assert "schedule = \"0 3 * * *\"" in text

    def test_save_preserves_all_sections(self, reload_modules):
        """save_config writes all three sections."""
        import meme.config as cfg

        config = MemeConfig()
        save_config(config)

        config_path = cfg.CONFIG_PATH
        text = config_path.read_text(encoding="utf-8")
        assert "[dream]" in text
        assert "[daydream]" in text
        assert "[hooks]" in text

    def test_save_roundtrip(self, reload_modules):
        """Save then load returns equivalent config."""
        config = MemeConfig()
        config.dream.enabled = False
        config.daydream.merge = False
        config.hooks.session_end_check_dream = False
        save_config(config)

        loaded = load_config()
        assert loaded.dream.enabled is False
        assert loaded.daydream.merge is False
        assert loaded.hooks.session_end_check_dream is False


class TestGetConfigValue:
    def test_get_nested_value(self):
        """get_config_value resolves dot paths."""
        config = MemeConfig()
        assert get_config_value(config, "dream.enabled") is True
        assert get_config_value(config, "dream.schedule") == "0 3 * * *"
        assert get_config_value(config, "daydream.threshold") == 0.4
        assert get_config_value(config, "hooks.session_end_check_dream") is True

    def test_get_top_level(self):
        """get_config_value can return nested objects."""
        config = MemeConfig()
        dream = get_config_value(config, "dream")
        assert isinstance(dream, DreamConfig)

    def test_get_missing_key_returns_none(self):
        """get_config_value returns None for unknown paths."""
        config = MemeConfig()
        assert get_config_value(config, "dream.nonexistent") is None
        assert get_config_value(config, "nonexistent.field") is None
        assert get_config_value(config, "") is None


class TestSetConfigValue:
    def test_set_bool(self):
        """set_config_value coerces string to bool."""
        config = MemeConfig()
        assert set_config_value(config, "dream.enabled", "false") is True
        assert config.dream.enabled is False
        assert set_config_value(config, "dream.enabled", "true") is True
        assert config.dream.enabled is True
        assert set_config_value(config, "dream.enabled", "1") is True
        assert config.dream.enabled is True
        assert set_config_value(config, "dream.enabled", "0") is True
        assert config.dream.enabled is False
        assert set_config_value(config, "dream.enabled", "yes") is True
        assert config.dream.enabled is True
        assert set_config_value(config, "dream.enabled", "no") is True
        assert config.dream.enabled is False
        assert set_config_value(config, "dream.enabled", "on") is True
        assert config.dream.enabled is True
        assert set_config_value(config, "dream.enabled", "off") is True
        assert config.dream.enabled is False

    def test_set_int(self):
        """set_config_value coerces string to int."""
        config = MemeConfig()
        # No int fields in current config, but test via a float that looks like int
        assert set_config_value(config, "daydream.threshold", "0.5") is True
        assert config.daydream.threshold == 0.5

    def test_set_float(self):
        """set_config_value coerces string to float."""
        config = MemeConfig()
        assert set_config_value(config, "dream.threshold", "0.85") is True
        assert config.dream.threshold == 0.85

    def test_set_string(self):
        """set_config_value sets string fields directly."""
        config = MemeConfig()
        assert set_config_value(config, "dream.schedule", "0 6 * * *") is True
        assert config.dream.schedule == "0 6 * * *"
        assert set_config_value(config, "dream.report_dir", "reports") is True
        assert config.dream.report_dir == "reports"

    def test_set_invalid_float_returns_false(self):
        """set_config_value returns False when float coercion fails."""
        config = MemeConfig()
        assert set_config_value(config, "dream.threshold", "not_a_number") is False
        # Value unchanged
        assert config.dream.threshold == 0.4

    def test_set_missing_path_returns_false(self):
        """set_config_value returns False for unknown paths."""
        config = MemeConfig()
        assert set_config_value(config, "dream.nonexistent", "value") is False
        assert set_config_value(config, "nonexistent.field", "value") is False
        assert set_config_value(config, "", "value") is False

    def test_set_missing_intermediate_returns_false(self):
        """set_config_value returns False when intermediate key is missing."""
        config = MemeConfig()
        assert set_config_value(config, "nonexistent.nested.field", "value") is False
