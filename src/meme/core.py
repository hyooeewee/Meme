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
from meme.cli import build_parser, main  # noqa: F401
from meme.commands.ingest import cmd_import, cmd_learn  # noqa: F401
from meme.commands.lifecycle import cmd_decay, cmd_demote, cmd_promote, cmd_warm  # noqa: F401
from meme.commands.links import (  # noqa: F401
    cmd_config,
    cmd_daydream,
    cmd_dream,
    cmd_link,
    cmd_suggest_links,
)
from meme.commands.maintenance import (  # noqa: F401
    cmd_backup,
    cmd_doctor,
    cmd_export,
    cmd_gc,
    cmd_reindex,
    cmd_stats,
)
from meme.commands.memory import (  # noqa: F401
    cmd_add,
    cmd_delete,
    cmd_edit,
    cmd_forget,
    cmd_list,
    cmd_query,
    cmd_search,
    cmd_show,
)

# Commands
from meme.commands.setup import cmd_init, cmd_setup, cmd_uninstall  # noqa: F401
from meme.commands.system import (  # noqa: F401
    cmd_auth,
    cmd_changelog,
    cmd_heat,
    cmd_run,
    cmd_upgrade,
    cmd_version,
)

# Configuration
from meme.config import (  # noqa: F401
    get_config_value,
    load_config,
    save_config,
    set_config_value,
)

# Constants
from meme.constants import (  # noqa: F401
    ARCHIVE_DIR,
    BACKUPS_DIR,
    BIN_DIR,
    COLD_DIR,
    CONFIG_PATH,
    CURRENT_SCHEMA,
    DECAY_LOG_PATH,
    FORGOTTEN_INDEX_PATH,
    FRONTMATTER_KEYS,
    GRAPH_PATH,
    IMPORT_STATE_PATH,
    INDEX_PATH,
    MEME_HOME,
    MEMORY_MD_PATH,
    META_DIR,
    SESSION_HEAT_PATH,
    SUBDIRS,
    TIER_ARCHIVE_THRESHOLD,
    TIER_WORKING_THRESHOLD,
    TOKEN_BUDGET_HOOK,
    TOKEN_BUDGET_WORKING,
    VAULT_DIR,
    VERSION_CHECK_PATH,
    VERSION_PATH,
    WORKING_DIR,
)

# Data models
from meme.models import (  # noqa: F401
    DaydreamConfig,
    DreamConfig,
    HooksConfig,
    MemeConfig,
    MemoryMeta,
    TierThresholds,
    TokenBudgets,
)

# Utilities
from meme.utils import (  # noqa: F401
    _add_to_graph,
    _get_package_resource_path,
    _remove_from_graph,
    _remove_from_index,
    _update_index_entry,
    count_tokens,
    create_memory_record,
    ensure_symlink,
    find_all_memories,
    find_memory_by_id,
    generate_id,
    get_memory_dir,
    get_tier,
    git_commit,
    git_run,
    load_forgotten_index,
    load_graph,
    load_index,
    load_memory,
    parse_frontmatter,
    rebuild_memory_md,
    render_frontmatter,
    save_forgotten_index,
    save_graph,
    save_index,
    save_memory,
)

# Vault
from meme.vault import (  # noqa: F401
    _get_vault_key,
    _touch_id_auth,
    load_vault_memory,
    parse_memory_string,
    save_memory_to_string,
    save_vault_memory,
    vault_decrypt,
    vault_encrypt,
)

if __name__ == "__main__":
    main()
