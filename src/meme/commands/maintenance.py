"""Meme CLI commands."""
import datetime
import json
import os
import re
import sys
from pathlib import Path

from meme.constants import (
    MEME_HOME, WORKING_DIR, ARCHIVE_DIR, COLD_DIR, VAULT_DIR,
    BACKUPS_DIR, META_DIR, BIN_DIR, MEMORY_MD_PATH,
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
# Command: doctor
# ========================================

def cmd_doctor(args):
    """Health check and auto-fix."""
    fix = args.fix or False
    ask = args.ask or False
    issues = []

    # Check symlinks
    claude_projects = Path.home() / ".claude" / "projects"
    if claude_projects.exists():
        for proj_dir in claude_projects.iterdir():
            memory_dir = proj_dir / "memory"
            for link_name in ["MEMORY.md", "working"]:
                link = memory_dir / link_name
                if link.is_symlink() and not link.exists():
                    issues.append(("broken_symlink", str(link)))
                    if fix:
                        target = MEMORY_MD_PATH if link_name == "MEMORY.md" else WORKING_DIR
                        link.unlink()
                        ensure_symlink(link, target)
                        print(f"  Fixed symlink: {link}")

    # Check frontmatter (skip vault .enc files — decrypting them requires auth)
    for p in find_all_memories(include_cold=True):
        if p.suffix == ".enc":
            continue
        try:
            meta, body = load_memory(p)
            missing = []
            for key in ["id", "type", "importance", "created"]:
                if key not in meta:
                    missing.append(key)
            if missing:
                issues.append(("missing_frontmatter", str(p), missing))
                if fix:
                    now = datetime.date.today().strftime("%Y-%m-%d")
                    meta.setdefault("id", generate_id("unknown", p.stem))
                    meta.setdefault("type", "feedback")
                    meta.setdefault("importance", 0.5)
                    meta.setdefault("created", now)
                    save_memory(p, meta, body)
                    print(f"  Fixed frontmatter: {p}")
        except Exception as e:
            issues.append(("corrupt_file", str(p), str(e)))

    # Check graph consistency
    graph = load_graph()
    for mem_id, links in graph.items():
        if not find_memory_by_id(mem_id):
            issues.append(("orphan_graph_node", mem_id))
            if fix:
                del graph[mem_id]
                save_graph(graph)
                print(f"  Removed orphan from graph: {mem_id}")
        for linked_id in links:
            if not find_memory_by_id(linked_id):
                issues.append(("broken_graph_link", mem_id, linked_id))

    # Check token budget
    total_tokens = 0
    for p in WORKING_DIR.glob("*.md"):
        try:
            _, body = load_memory(p)
            total_tokens += count_tokens(body)
        except Exception as e:
            from meme.log import get_logger
            get_logger('meme').warning(f'Command error in {os.path.basename(p)}: {e}')
            continue
    if total_tokens > TOKEN_BUDGET_WORKING:
        issues.append(("token_overbudget", total_tokens, TOKEN_BUDGET_WORKING))

    # Report
    if not issues:
        print("All checks passed. No issues found.")
    else:
        print(f"Found {len(issues)} issue(s):\n")
        for issue in issues:
            print(f"  - {issue[0]}: {issue[1:]}")
        if not fix:
            print("\nRun 'meme doctor --fix' to auto-fix what's possible.")

# ========================================
# Command: backup / gc
# ========================================

def cmd_backup(args):
    """Create a backup tarball."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUPS_DIR / f"meme_backup_{ts}.tar.gz"

    with tarfile.open(backup_path, "w:gz") as tar:
        for item in MEME_HOME.iterdir():
            if item.name in ("backups", ".git", ".upgrade-tmp"):
                continue
            tar.add(item, arcname=item.name)

    size_mb = backup_path.stat().st_size / (1024 * 1024)
    print(f"Backup created: {backup_path} ({size_mb:.1f} MB)")


def cmd_gc(args):
    """Clean old backups (keep 7 daily, 4 weekly, 3 monthly)."""
    if not BACKUPS_DIR.exists():
        print("No backups directory.")
        return

    backups = sorted(BACKUPS_DIR.glob("meme_backup_*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    now = datetime.datetime.now()
    keep = set()

    for bp in backups:
        mtime = datetime.datetime.fromtimestamp(bp.stat().st_mtime)
        age_days = (now - mtime).days

        if age_days <= 7:
            keep.add(bp)  # Keep all from last 7 days
        elif age_days <= 30:
            # Keep 1 per week
            week = mtime.isocalendar()[1]
            key = f"week_{week}"
            if key not in keep:
                keep.add(bp)
                keep.add(key)
        elif age_days <= 90:
            # Keep 1 per month
            key = f"month_{mtime.month}"
            if key not in keep:
                keep.add(bp)
                keep.add(key)

    # Filter out string keys
    keep_files = {p for p in keep if isinstance(p, Path)}

    removed = 0
    for bp in backups:
        if bp not in keep_files:
            bp.unlink()
            removed += 1

    print(f"Cleaned {removed} old backups. Kept {len(keep_files)}.")

# ========================================
# Command: reindex
# ========================================

def cmd_reindex(args):
    """Rebuild index.json and graph.json from memory files."""
    index = {}
    graph = {}

    for p in find_all_memories(include_cold=True):
        if p.suffix == ".enc":
            continue
        try:
            meta, body = load_memory(p)
            mem_id = meta.get("id")
            if not mem_id:
                continue
            _update_index_entry(mem_id, meta, p, index=index)
            links = meta.get("links", [])
            if links:
                graph[mem_id] = links
        except Exception as e:
            from meme.log import get_logger
            get_logger('meme').warning(f'Command error in {os.path.basename(p)}: {e}')
            continue

    save_index(index)
    save_graph(graph)
    rebuild_memory_md()
    git_commit("reindex: rebuilt index and graph")
    print(f"Reindexed: {len(index)} memories, {len(graph)} graph nodes.")

# ========================================
# Command: stats
# ========================================

def cmd_stats(args):
    """Show memory statistics."""
    stats = {"working": 0, "archive": 0, "cold": 0, "total": 0}
    types = {}
    total_tokens = 0

    for p in find_all_memories(include_cold=True):
        if p.suffix == ".enc":
            continue
        try:
            meta, body = load_memory(p)
            if meta.get("forgotten"):
                continue
            tier = get_tier(meta)
            stats[tier] = stats.get(tier, 0) + 1
            stats["total"] += 1
            t = meta.get("type", "unknown")
            types[t] = types.get(t, 0) + 1
            total_tokens += count_tokens(body)
        except Exception as e:
            from meme.log import get_logger
            get_logger('meme').warning(f'Command error in {os.path.basename(p)}: {e}')
            continue

    graph = load_graph()
    total_links = sum(len(v) for v in graph.values())

    print("Meme Statistics:")
    print(f"  Working:  {stats['working']}")
    print(f"  Archive:  {stats['archive']}")
    print(f"  Cold:     {stats['cold']}")
    print(f"  Total:    {stats['total']}")
    print(f"  Links:    {total_links}")
    print(f"  Tokens:   ~{total_tokens}")
    print(f"\n  By type:")
    for t, count in sorted(types.items()):
        print(f"    {t}: {count}")

# ========================================
# Command: export
# ========================================

def cmd_export(args):
    """Export all memories."""
    fmt = args.format or "json"
    memories = []

    for p in find_all_memories(include_cold=True):
        if p.suffix == ".enc":
            continue
        try:
            meta, body = load_memory(p)
            if meta.get("forgotten"):
                continue
            memories.append({"meta": meta, "body": body})
        except Exception as e:
            from meme.log import get_logger
            get_logger('meme').warning(f'Command error in {os.path.basename(p)}: {e}')
            continue

    if fmt == "json":
        output = json.dumps(memories, indent=2, ensure_ascii=False)
    else:
        lines = []
        for m in memories:
            meta = m["meta"]
            lines.append(f"# {meta.get('id', 'unknown')}")
            lines.append(f"Type: {meta.get('type')}, Importance: {meta.get('importance')}")
            lines.append(f"Tags: {', '.join(meta.get('tags', []))}")
            lines.append("")
            lines.append(m["body"])
            lines.append("\n---\n")
        output = "\n".join(lines)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Exported {len(memories)} memories to {args.output}")
    else:
        print(output)

