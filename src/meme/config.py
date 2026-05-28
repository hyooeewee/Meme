"""Meme configuration management."""
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from meme.constants import CONFIG_PATH


# ========================================
# Configuration
# ========================================

DEFAULT_CONFIG: dict = {
    "dream": {
        "enabled": True,
        "schedule": "0 3 * * *",
        "threshold": 0.4,
        "auto_apply": True,
        "mode": "all",
        "report_dir": "dreams",
    },
    "daydream": {
        "threshold": 0.4,
        "default_mode": "all",
    },
    "hooks": {
        "session_end_check_dream": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config() -> dict:
    """Load user config merged with defaults."""
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            text = CONFIG_PATH.read_text(encoding="utf-8")
            user = tomllib.loads(text)
            config = _deep_merge(config, user)
        except Exception:
            pass
    return config


def save_config(config: dict):
    """Save config to disk (preserving comments is not supported)."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Meme configuration", ""]
    for section, values in config.items():
        lines.append(f"[{section}]")
        for key, val in values.items():
            if isinstance(val, bool):
                lines.append(f'{key} = {str(val).lower()}')
            elif isinstance(val, str):
                lines.append(f'{key} = "{val}"')
            elif isinstance(val, (int, float)):
                lines.append(f'{key} = {val}')
            elif isinstance(val, list):
                items = ', '.join(f'"{v}"' for v in val)
                lines.append(f'{key} = [{items}]')
        lines.append("")
    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")


def get_config_value(config: dict, key_path: str):
    """Get a config value by dot path, e.g. 'dream.enabled'."""
    keys = key_path.split(".")
    val = config
    for k in keys:
        if isinstance(val, dict) and k in val:
            val = val[k]
        else:
            return None
    return val


def set_config_value(config: dict, key_path: str, value) -> bool:
    """Set a config value by dot path. Returns True if successful."""
    keys = key_path.split(".")
    val = config
    for k in keys[:-1]:
        if k not in val:
            val[k] = {}
        val = val[k]
    # Type coercion based on existing value in defaults
    target_key = keys[-1]
    existing = get_config_value(DEFAULT_CONFIG, key_path)
    if existing is not None:
        if isinstance(existing, bool):
            value = value.lower() in ("true", "1", "yes", "on")
        elif isinstance(existing, (int, float)):
            try:
                value = float(value)
                if isinstance(existing, int):
                    value = int(value)
            except ValueError:
                return False
    val[target_key] = value
    return True

