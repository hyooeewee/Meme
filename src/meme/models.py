"""Meme data models — typed dataclasses for config and metadata."""

import re
from dataclasses import asdict, dataclass, field
from typing import Literal

# ========================================
# Configuration
# ========================================


class ConfigValidationError(ValueError):
    """Raised when config validation fails."""

    pass


# Valid cron schedule: 5 fields (minute hour day month weekday)
# Supports: * , - / and numeric values
_CRON_RE = re.compile(
    r"^(\*|\d+)(\s+(\*|\d+)){4}$" r"|^(\*|\d+|\d+-\d+|\d+/\d+|\d+,\d+)(\s+(\*|\d+|\d+-\d+|\d+/\d+|\d+,\d+)){4}$"
)


def _validate_cron(schedule: str) -> None:
    """Validate a 5-field cron expression."""
    parts = schedule.split()
    if len(parts) != 5:
        raise ConfigValidationError(
            f"Invalid cron schedule '{schedule}': expected 5 fields (minute hour day month weekday), got {len(parts)}"
        )
    for i, part in enumerate(parts):
        if part == "*":
            continue
        # Allow: single number, range (1-5), step (*/5), list (1,3,5)
        if re.match(r"^(\*|\d+|\d+-\d+|\d+/\d+|\d+,\d+)$", part):
            continue
        field_names = ["minute", "hour", "day", "month", "weekday"]
        raise ConfigValidationError(
            f"Invalid cron field '{part}' in position {i} ({field_names[i]}). "
            f"Expected *, number, range (1-5), step (*/5), or list (1,3)."
        )


def _validate_threshold(value: float, name: str) -> None:
    """Validate a threshold value (0.0 to 1.0)."""
    if not isinstance(value, (int, float)):
        raise ConfigValidationError(f"{name} must be a number, got {type(value).__name__}")
    if not 0.0 <= float(value) <= 1.0:
        raise ConfigValidationError(f"{name} must be between 0.0 and 1.0, got {value}")


def _validate_mode(value: str, name: str) -> None:
    """Validate a mode literal."""
    valid = {"all", "cluster", "link"}
    if value not in valid:
        raise ConfigValidationError(f"{name} must be one of {sorted(valid)}, got '{value}'")


@dataclass
class DreamConfig:
    enabled: bool = True
    schedule: str = "0 3 * * *"
    threshold: float = 0.4
    auto_apply: bool = True
    mode: Literal["all", "cluster", "link"] = "all"
    report_dir: str = "dreams"

    def validate(self) -> None:
        """Validate dream config fields."""
        _validate_cron(self.schedule)
        _validate_threshold(self.threshold, "dream.threshold")
        _validate_mode(self.mode, "dream.mode")
        if not isinstance(self.report_dir, str) or not self.report_dir:
            raise ConfigValidationError(f"dream.report_dir must be a non-empty string, got {self.report_dir!r}")


@dataclass
class DaydreamConfig:
    threshold: float = 0.4
    default_mode: Literal["all", "cluster", "link"] = "all"
    auto_apply: bool = True
    merge: bool = True

    def validate(self) -> None:
        """Validate daydream config fields."""
        _validate_threshold(self.threshold, "daydream.threshold")
        _validate_mode(self.default_mode, "daydream.default_mode")


@dataclass
class HooksConfig:
    session_end_check_dream: bool = True

    def validate(self) -> None:
        """Validate hooks config fields."""
        pass  # All fields are booleans with no constraints


@dataclass
class MemeConfig:
    dream: DreamConfig = field(default_factory=DreamConfig)
    daydream: DaydreamConfig = field(default_factory=DaydreamConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "MemeConfig":
        """Build from a nested dict (e.g. parsed TOML)."""
        dream = DreamConfig(**data.get("dream", {}))
        daydream = DaydreamConfig(**data.get("daydream", {}))
        hooks = HooksConfig(**data.get("hooks", {}))
        return cls(dream=dream, daydream=daydream, hooks=hooks)

    def validate(self) -> None:
        """Validate all config sections."""
        self.dream.validate()
        self.daydream.validate()
        self.hooks.validate()

    def to_dict(self) -> dict:
        """Serialize to nested dict for TOML output."""
        return {
            "dream": asdict(self.dream),
            "daydream": asdict(self.daydream),
            "hooks": asdict(self.hooks),
        }

    def items(self):
        """Dict-like interface for backward compatibility."""
        return self.to_dict().items()

    def get(self, key: str, default=None):
        """Dict-like get for backward compatibility."""
        return getattr(self, key, default)


# ========================================
# Memory Frontmatter
# ========================================


@dataclass
class MemoryMeta:
    """YAML frontmatter for a memory file."""

    id: str = ""
    type: str = "feedback"
    importance: float = 0.5
    created: str = ""
    last_accessed: str = ""
    access_count: int = 0
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    superseded_by: str | None = None
    supersedes: str | None = None
    forgotten: bool = False
    forgotten_at: str | None = None
    forgotten_reason: str | None = None
    sensitive: bool = False
    source_url: str | None = None
    source_file: str | None = None
    corrects: str | None = None
    scope: str | None = None
    wrong_pattern: str | None = None
    correct_pattern: str | None = None

    # Dict-like interface for backward compatibility during migration
    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key: str):
        return getattr(self, key)

    def __setitem__(self, key: str, value):
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def keys(self):
        return [f.name for f in self.__dataclass_fields__.values()]

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryMeta":
        """Build from a dict, ignoring unknown keys."""
        import datetime as _dt

        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {}
        for k, v in data.items():
            if k not in known:
                continue
            # YAML parses ISO dates into date/datetime objects; coerce back to str
            if isinstance(v, (_dt.date, _dt.datetime)):
                v = v.isoformat()
            filtered[k] = v
        return cls(**filtered)

    def to_dict(self) -> dict:
        """Serialize to dict, skipping None values and empty lists."""
        result: dict[str, object] = {}
        for k, v in asdict(self).items():
            if v is None:
                continue
            if isinstance(v, list) and not v:
                result[k] = []
                continue
            result[k] = v
        return result


# ========================================
# System Constants
# ========================================


@dataclass(frozen=True)
class TierThresholds:
    working: float = 0.8
    archive: float = 0.2


@dataclass(frozen=True)
class TokenBudgets:
    working: int = 2000
    hook: int = 8000
