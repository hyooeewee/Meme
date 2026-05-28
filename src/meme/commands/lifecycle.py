"""Meme CLI commands."""
import datetime
import json
import os
import re
import sys
from pathlib import Path

from meme.constants import (
    MEME_HOME, WORKING_DIR, ARCHIVE_DIR, COLD_DIR, VAULT_DIR,
    BACKUPS_DIR, META_DIR, BIN_DIR,
    FRONTMATTER_KEYS, SUBDIRS,
    TIER_WORKING_THRESHOLD, TIER_ARCHIVE_THRESHOLD,
    TOKEN_BUDGET_WORKING, TOKEN_BUDGET_HOOK,
)
from meme.config import load_config, save_config, get_config_value, set_config_value
from meme.utils import (
    parse_frontmatter, render_frontmatter,
    load_memory, save_memory, count_tokens, generate_id,
    git_run, git_commit,
    load_index, save_index, load_graph, save_graph,
    load_forgotten_index, save_forgotten_index,
    ensure_symlink, find_all_memories, _is_forgotten,
    find_memory_by_id, get_tier, get_memory_dir,
    rebuild_memory_md, _update_index_entry,
    _remove_from_index, _add_to_graph, _remove_from_graph,
    _get_package_resource_path,
)
from meme.vault import (
    _touch_id_auth, _get_vault_key,
    vault_encrypt, vault_decrypt,
    save_vault_memory, load_vault_memory,
    save_memory_to_string, parse_memory_string,
)

# ========================================
# Command: decay
# ========================================

def cmd_decay(args):
    """Run importance decay scan."""
    dry_run = args.dry_run or False
    now = datetime.date.today()
    decayed = 0

    for p in find_all_memories(include_cold=True):
        if p.suffix == ".enc":
            continue
        try:
            meta, body = load_memory(p)
            if meta.get("forgotten"):
                continue

            last = meta.get("last_accessed", meta.get("created", ""))
            if not last:
                continue
            last_date = datetime.date.fromisoformat(last)
            days = (now - last_date).days
            if days <= 0:
                continue

            old_imp = meta.get("importance", 0.5)
            # Correction memories decay slower
            decay_rate = 0.975 if meta.get("type") == "correction" else 0.95
            new_imp = old_imp * (decay_rate ** days)
            new_imp = round(new_imp, 4)

            if new_imp != old_imp:
                if not dry_run:
                    meta["importance"] = new_imp
                    save_memory(p, meta, body)
                    _update_index_entry(meta["id"], meta, p)

                    # Auto-migrate tiers
                    old_tier = get_tier({"importance": old_imp})
                    new_tier = get_tier({"importance": new_imp})
                    if old_tier != new_tier:
                        new_dir = get_memory_dir(meta.get("type", "feedback"), new_tier)
                        new_path = new_dir / p.name
                        if not new_path.exists():
                            p.rename(new_path)

                    # Log decay
                    with open(DECAY_LOG_PATH, "a") as f:
                        f.write(json.dumps({
                            "ts": datetime.datetime.now().isoformat(),
                            "id": meta["id"],
                            "old": old_imp,
                            "new": new_imp,
                            "days": days,
                        }) + "\n")

                decayed += 1
                print(f"  {meta['id']}: {old_imp:.3f} -> {new_imp:.3f} ({days} days)")

        except Exception as e:
            from meme.log import get_logger
            get_logger('meme').warning(f'Command error in {os.path.basename(path)}: {e}')
            continue

    if not dry_run and decayed:
        rebuild_memory_md()
        git_commit(f"decay: {decayed} memories updated")

    action = "Would decay" if dry_run else "Decayed"
    print(f"\n{action} {decayed} memories.")

# ========================================
# Command: promote / demote / warm
# ========================================

def cmd_promote(args):
    """Manually promote a memory to working tier."""
    mem_id = args.id
    path = find_memory_by_id(mem_id)
    if not path:
        print(f"Memory not found: {mem_id}")
        return
    meta, body = load_memory(path)
    meta["importance"] = max(meta.get("importance", 0.5), TIER_WORKING_THRESHOLD)
    new_dir = get_memory_dir(meta.get("type", "feedback"), "working")
    new_path = new_dir / path.name
    if new_path != path:
        if new_path.exists():
            print(f"Target already exists: {new_path}")
            return
        path.rename(new_path)
        save_memory(new_path, meta, body)
        _update_index_entry(mem_id, meta, new_path)
    else:
        save_memory(path, meta, body)
        _update_index_entry(mem_id, meta, path)
    rebuild_memory_md()
    git_commit(f"promote: {mem_id}")
    print(f"Promoted {mem_id} to working tier.")


def cmd_demote(args):
    """Manually demote a memory."""
    mem_id = args.id
    path = find_memory_by_id(mem_id)
    if not path:
        print(f"Memory not found: {mem_id}")
        return
    meta, body = load_memory(path)
    new_importance = args.importance or max(meta.get("importance", 0.5) - 0.2, 0.05)
    meta["importance"] = new_importance
    new_tier = get_tier(meta)
    new_dir = get_memory_dir(meta.get("type", "feedback"), new_tier)
    new_path = new_dir / path.name
    if new_path != path and not new_path.exists():
        path.rename(new_path)
        save_memory(new_path, meta, body)
        _update_index_entry(mem_id, meta, new_path)
    else:
        save_memory(path, meta, body)
        _update_index_entry(mem_id, meta, path)
    rebuild_memory_md()
    git_commit(f"demote: {mem_id}")
    print(f"Demoted {mem_id} to {new_tier} (importance={new_importance:.2f}).")


def cmd_warm(args):
    """Warm a cold memory back to archive."""
    mem_id = args.id
    path = find_memory_by_id(mem_id)
    if not path:
        print(f"Memory not found: {mem_id}")
        return
    meta, body = load_memory(path)
    meta["importance"] = max(meta.get("importance", 0.1), 0.25)
    new_dir = get_memory_dir(meta.get("type", "feedback"), "archive")
    new_path = new_dir / path.name
    if new_path != path and not new_path.exists():
        path.rename(new_path)
        save_memory(new_path, meta, body)
        _update_index_entry(mem_id, meta, new_path)
    else:
        save_memory(path, meta, body)
        _update_index_entry(mem_id, meta, path)
    rebuild_memory_md()
    git_commit(f"warm: {mem_id}")
    print(f"Warmed {mem_id} to archive (importance={meta['importance']:.2f}).")

# ========================================
# Command: link
# ========================================

def cmd_link(args):
    """Create a link between two memories."""
    id_a, id_b = args.id_a, args.id_b
    for mid in [id_a, id_b]:
        if not find_memory_by_id(mid):
            print(f"Memory not found: {mid}")
            return

    _add_to_graph(id_a, [id_b])
    _add_to_graph(id_b, [id_a])

    # Also update frontmatter links
    for mid in [id_a, id_b]:
        path = find_memory_by_id(mid)
        meta, body = load_memory(path)
        links = meta.get("links", [])
        other = id_b if mid == id_a else id_a
        if other not in links:
            links.append(other)
            meta["links"] = links
            save_memory(path, meta, body)

    git_commit(f"link: {id_a} <-> {id_b}")
    print(f"Linked: {id_a} <-> {id_b}")


