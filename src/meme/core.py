# meme/core.py — Compatibility re-export layer
#
# This module re-exports all public APIs from the new modular structure.
# It is kept for backward compatibility with existing integrations
# (pyproject.toml entry point, hooks, external scripts).
#
# New code should import directly from the submodules:
#   from meme.constants import MEME_HOME
#   from meme.utils import load_memory
#   from meme.commands.memory import cmd_add

"""Meme — A centralized, tiered memory system with knowledge graph."""

# CLI entry point
from meme.cli import build_parser, main

# Constants
from meme.constants import (
    MEME_HOME, WORKING_DIR, ARCHIVE_DIR, COLD_DIR, VAULT_DIR,
    BACKUPS_DIR, META_DIR, BIN_DIR, INDEX_PATH, GRAPH_PATH,
    VERSION_PATH, IMPORT_STATE_PATH, SESSION_HEAT_PATH,
    CONFLICT_LOG_PATH, DECAY_LOG_PATH, FORGOTTEN_INDEX_PATH,
    VERSION_CHECK_PATH, MEMORY_MD_PATH, CURRENT_SCHEMA,
    TOKEN_BUDGET_WORKING, TOKEN_BUDGET_HOOK,
    TIER_WORKING_THRESHOLD, TIER_ARCHIVE_THRESHOLD,
    SUBDIRS, FRONTMATTER_KEYS, CONFIG_PATH,
)

# Configuration
from meme.config import (
    DEFAULT_CONFIG, _deep_merge, load_config, save_config,
    get_config_value, set_config_value,
)

# Utilities
from meme.utils import (
    _get_package_resource_path,
    parse_frontmatter, render_frontmatter,
    load_memory, save_memory, count_tokens, generate_id,
    git_run, git_commit,
    load_index, save_index, load_graph, save_graph,
    load_forgotten_index, save_forgotten_index,
    ensure_symlink, find_all_memories, _is_forgotten,
    find_memory_by_id, get_tier, get_memory_dir,
    rebuild_memory_md, _update_index_entry,
    _remove_from_index, _add_to_graph, _remove_from_graph,
)

# Vault
from meme.vault import (
    _touch_id_auth, _get_vault_key,
    vault_encrypt, vault_decrypt,
    save_vault_memory, load_vault_memory,
    save_memory_to_string, parse_memory_string,
)

# Commands
from meme.commands.setup import cmd_setup, cmd_init, cmd_uninstall
from meme.commands.memory import (
    cmd_add, cmd_list, cmd_show, cmd_search, cmd_query,
    cmd_edit, cmd_delete, cmd_forget,
)
from meme.commands.ingest import cmd_learn, cmd_import
from meme.commands.lifecycle import cmd_decay, cmd_promote, cmd_demote, cmd_warm
from meme.commands.links import cmd_link, cmd_suggest_links, cmd_daydream, cmd_config, cmd_dream
from meme.commands.maintenance import (
    cmd_doctor, cmd_backup, cmd_gc, cmd_reindex,
    cmd_stats, cmd_export,
)
from meme.commands.system import cmd_version, cmd_upgrade, cmd_changelog, cmd_auth, cmd_run, cmd_heat
