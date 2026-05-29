"""Meme configuration management."""

import tomllib

from meme.constants import CONFIG_PATH
from meme.models import ConfigValidationError, MemeConfig

# ========================================
# Configuration
# ========================================


def load_config() -> MemeConfig:
    """Load user config merged with defaults.

    Validates the config and raises ConfigValidationError for invalid values.
    On invalid TOML syntax, falls back to defaults with a warning.
    """
    config = MemeConfig()
    if CONFIG_PATH.exists():
        try:
            text = CONFIG_PATH.read_text(encoding="utf-8")
            user = tomllib.loads(text)
            config = MemeConfig.from_dict(user)
            config.validate()
        except ConfigValidationError:
            raise
        except Exception as e:
            from meme.log import get_logger

            get_logger("meme.config").warning(f"Config load failed: {e}")
    return config


def save_config(config: MemeConfig):
    """Save config to disk (preserving comments is not supported)."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = config.to_dict()
    lines = ["# Meme configuration", ""]
    for section, values in data.items():
        lines.append(f"[{section}]")
        for key, val in values.items():
            if isinstance(val, bool):
                lines.append(f"{key} = {str(val).lower()}")
            elif isinstance(val, str):
                lines.append(f'{key} = "{val}"')
            elif isinstance(val, (int, float)):
                lines.append(f"{key} = {val}")
            elif isinstance(val, list):
                items = ", ".join(f'"{v}"' for v in val)
                lines.append(f"{key} = [{items}]")
        lines.append("")
    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")


def get_config_value(config: MemeConfig, key_path: str):
    """Get a config value by dot path, e.g. 'dream.enabled'."""
    keys = key_path.split(".")
    val = config
    for k in keys:
        if hasattr(val, k):
            val = getattr(val, k)
        else:
            return None
    return val


def set_config_value(config: MemeConfig, key_path: str, value: str) -> bool:
    """Set a config value by dot path. Returns True if successful."""
    keys = key_path.split(".")
    target_obj = config
    for k in keys[:-1]:
        if not hasattr(target_obj, k):
            return False
        target_obj = getattr(target_obj, k)

    target_key = keys[-1]
    if not hasattr(target_obj, target_key):
        return False

    existing = getattr(target_obj, target_key)
    # Type coercion
    coerced: bool | int | float | str
    if isinstance(existing, bool):
        coerced = value.lower() in ("true", "1", "yes", "on")
    elif isinstance(existing, (int, float)):
        try:
            coerced = float(value)
            if isinstance(existing, int):
                coerced = int(coerced)
        except ValueError:
            return False
    else:
        coerced = value

    setattr(target_obj, target_key, coerced)
    return True
