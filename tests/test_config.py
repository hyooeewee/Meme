# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///

"""Tests for Meme configuration management."""

import pytest

from meme.config import get_config_value, load_config, save_config, set_config_value
from meme.models import ConfigValidationError, DreamConfig, MemeConfig


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
            '[dream]\nenabled = false\nschedule = "0 5 * * *"\nthreshold = 0.7\n' "[daydream]\nmerge = false\n",
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
        assert 'schedule = "0 3 * * *"' in text

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


class TestConfigValidation:
    """Tests for config schema validation."""

    def test_valid_config_loads(self, reload_modules):
        """Valid config loads without error."""
        import meme.config as cfg

        config_path = cfg.CONFIG_PATH
        config_path.write_text(
            '[dream]\nenabled = false\nschedule = "0 5 * * *"\nthreshold = 0.7\n'
            '[daydream]\nthreshold = 0.5\ndefault_mode = "cluster"\n',
            encoding="utf-8",
        )
        config = load_config()
        assert config.dream.enabled is False
        assert config.dream.threshold == 0.7
        assert config.daydream.default_mode == "cluster"

    def test_invalid_cron_raises(self, reload_modules):
        """Invalid cron schedule raises ConfigValidationError."""
        import meme.config as cfg

        config_path = cfg.CONFIG_PATH
        config_path.write_text('[dream]\nschedule = "not-a-cron"\n', encoding="utf-8")
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config()
        assert "cron" in str(exc_info.value).lower()

    def test_cron_wrong_field_count_raises(self, reload_modules):
        """Cron with wrong field count raises ConfigValidationError."""
        import meme.config as cfg

        config_path = cfg.CONFIG_PATH
        config_path.write_text('[dream]\nschedule = "0 3 * *"\n', encoding="utf-8")
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config()
        assert "5 fields" in str(exc_info.value)

    def test_invalid_dream_threshold_raises(self, reload_modules):
        """Dream threshold outside 0-1 raises ConfigValidationError."""
        import meme.config as cfg

        config_path = cfg.CONFIG_PATH
        config_path.write_text("[dream]\nthreshold = 1.5\n", encoding="utf-8")
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config()
        assert "threshold" in str(exc_info.value).lower()
        assert "0.0" in str(exc_info.value)

    def test_negative_threshold_raises(self, reload_modules):
        """Negative threshold raises ConfigValidationError."""
        import meme.config as cfg

        config_path = cfg.CONFIG_PATH
        config_path.write_text("[daydream]\nthreshold = -0.1\n", encoding="utf-8")
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config()
        assert "threshold" in str(exc_info.value).lower()

    def test_invalid_mode_raises(self, reload_modules):
        """Invalid mode literal raises ConfigValidationError."""
        import meme.config as cfg

        config_path = cfg.CONFIG_PATH
        config_path.write_text('[dream]\nmode = "invalid"\n', encoding="utf-8")
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config()
        assert "mode" in str(exc_info.value).lower()
        assert "invalid" in str(exc_info.value).lower()

    def test_invalid_daydream_mode_raises(self, reload_modules):
        """Invalid daydream mode raises ConfigValidationError."""
        import meme.config as cfg

        config_path = cfg.CONFIG_PATH
        config_path.write_text('[daydream]\ndefault_mode = "foo"\n', encoding="utf-8")
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config()
        assert "default_mode" in str(exc_info.value).lower()

    def test_empty_report_dir_raises(self, reload_modules):
        """Empty report_dir raises ConfigValidationError."""
        import meme.config as cfg

        config_path = cfg.CONFIG_PATH
        config_path.write_text('[dream]\nreport_dir = ""\n', encoding="utf-8")
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config()
        assert "report_dir" in str(exc_info.value).lower()

    def test_invalid_toml_still_falls_back(self, reload_modules):
        """Invalid TOML (not validation error) falls back to defaults."""
        import meme.config as cfg

        config_path = cfg.CONFIG_PATH
        config_path.write_text("not valid toml [[", encoding="utf-8")
        config = load_config()
        assert isinstance(config, MemeConfig)
        assert config.dream.enabled is True

    def test_config_validate_method_direct(self):
        """MemeConfig.validate() works directly."""
        config = MemeConfig()
        config.validate()  # Should not raise

    def test_config_validate_catches_invalid_direct(self):
        """MemeConfig.validate() catches invalid values set directly."""
        config = MemeConfig()
        config.dream.threshold = 2.0
        with pytest.raises(ConfigValidationError):
            config.validate()
