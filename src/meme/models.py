"""Meme data models — typed dataclasses for config and metadata."""

from dataclasses import dataclass, field, asdict
from typing import Literal


# ========================================
# Configuration
# ========================================

@dataclass
class DreamConfig:
    enabled: bool = True
    schedule: str = "0 3 * * *"
    threshold: float = 0.4
    auto_apply: bool = True
    mode: Literal["all", "cluster", "link"] = "all"
    report_dir: str = "dreams"


@dataclass
class DaydreamConfig:
    threshold: float = 0.4
    default_mode: Literal["all", "cluster", "link"] = "all"
    auto_apply: bool = True
    merge: bool = True


@dataclass
class HooksConfig:
    session_end_check_dream: bool = True


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
        result = {}
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
