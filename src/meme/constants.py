"""Meme constants and directory layout."""

import os
from pathlib import Path

# ========================================
# Constants
# ========================================

MEME_HOME = Path(os.environ.get("MEME_HOME", str(Path.home() / ".meme")))
WORKING_DIR = MEME_HOME / "working"
ARCHIVE_DIR = MEME_HOME / "archive"
COLD_DIR = MEME_HOME / "cold"
VAULT_DIR = MEME_HOME / "vault"
BACKUPS_DIR = MEME_HOME / "backups"
META_DIR = MEME_HOME / "meta"
BIN_DIR = MEME_HOME / "bin"

INDEX_PATH = META_DIR / "index.json"
GRAPH_PATH = META_DIR / "graph.json"
VERSION_PATH = META_DIR / "version.json"
IMPORT_STATE_PATH = META_DIR / "import_state.json"
SESSION_HEAT_PATH = META_DIR / "session_heat.json"
CONFLICT_LOG_PATH = META_DIR / "conflict_log.jsonl"
DECAY_LOG_PATH = META_DIR / "decay_log.jsonl"
FORGOTTEN_INDEX_PATH = META_DIR / "forgotten_index.json"
VERSION_CHECK_PATH = META_DIR / "version_check.json"

MEMORY_MD_PATH = MEME_HOME / "MEMORY.md"
CONFIG_PATH = MEME_HOME / "config.toml"

CURRENT_SCHEMA = 1

TOKEN_BUDGET_WORKING = 2000  # tokens
TOKEN_BUDGET_HOOK = 8000  # chars for additionalContext

TIER_WORKING_THRESHOLD = 0.8
TIER_ARCHIVE_THRESHOLD = 0.2

SUBDIRS = {
    "feedback": ARCHIVE_DIR / "feedback",
    "project": ARCHIVE_DIR / "projects",
    "knowledge": ARCHIVE_DIR / "knowledge",
    "correction": ARCHIVE_DIR / "corrections",
    "user": WORKING_DIR,
    "reference": ARCHIVE_DIR / "feedback",
}

FRONTMATTER_KEYS = [
    "id",
    "type",
    "importance",
    "created",
    "last_accessed",
    "access_count",
    "tags",
    "links",
    "superseded_by",
    "supersedes",
    "forgotten",
    "forgotten_at",
    "forgotten_reason",
    "sensitive",
    "source_url",
    "source_file",
    "corrects",
    "scope",
    "wrong_pattern",
    "correct_pattern",
]
