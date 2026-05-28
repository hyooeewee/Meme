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
from meme.config import load_config, save_config, get_config_value, set_config_value, DEFAULT_CONFIG
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
# Command: uninstall
# ========================================

def cmd_uninstall(args):
    """Uninstall the Meme system."""
    if not MEME_HOME.exists():
        print("Meme is not installed.")
        return

    # Remove hooks from settings.json
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            hooks = settings.get("hooks", {})
            for event in ["SessionStart", "UserPromptSubmit", "SessionEnd"]:
                if event in hooks:
                    hooks[event] = [
                        cfg for cfg in hooks[event]
                        if not any("meme" in h.get("command", "") for h in cfg.get("hooks", []))
                    ]
            settings["hooks"] = hooks
            settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
        except Exception:
            pass

    # Remove project symlinks
    claude_projects = Path.home() / ".claude" / "projects"
    if claude_projects.exists():
        for proj_dir in claude_projects.iterdir():
            memory_dir = proj_dir / "memory"
            for link_name in ["MEMORY.md", "working"]:
                link = memory_dir / link_name
                if link.is_symlink():
                    link.unlink()

    # Remove Obsidian symlink if recorded
    try:
        vd = json.loads(VERSION_PATH.read_text())
        obsidian_path = vd.get("obsidian_path")
        if obsidian_path:
            meme_link = Path(obsidian_path) / "Meme"
            if meme_link.is_symlink():
                meme_link.unlink()
                print(f"  Removed Obsidian symlink: {meme_link}")
    except Exception:
        pass

    # Remove PATH entry from shell rc files
    _remove_path_entry()

    keep_data = getattr(args, "keep_data", False)
    if keep_data:
        print(f"Hooks and symlinks removed. Data preserved at {MEME_HOME}")
    else:
        shutil.rmtree(MEME_HOME)
        print(f"Meme completely removed from {MEME_HOME}")


def _remove_path_entry():
    """Remove meme PATH entry from shell rc files."""
    # Support both old (# Meme CLI) and new (# meme-memory-system) markers
    markers = ["# meme-memory-system", "# Meme CLI"]
    for rc_name in [".zshrc", ".bash_profile", ".profile"]:
        rc_file = Path.home() / rc_name
        if not rc_file.exists():
            continue
        try:
            lines = rc_file.read_text(encoding="utf-8").splitlines()
            new_lines = []
            skip_next = False
            for line in lines:
                if any(m in line for m in markers):
                    skip_next = True
                    continue
                if skip_next and line.strip().startswith("export PATH") and ".meme" in line:
                    skip_next = False
                    continue
                if skip_next and line.strip().startswith("set -gx PATH") and ".meme" in line:
                    skip_next = False
                    continue
                skip_next = False
                new_lines.append(line)
            rc_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception:
            pass

# ========================================
# Command: add
# ========================================

def cmd_add(args):
    """Add a new memory."""
    content = args.content
    mem_type = args.type or "feedback"
    importance = args.importance or 0.6
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    links = [l.strip() for l in (args.links or "").split(",") if l.strip()]
    sensitive = args.sensitive or False

    # Generate ID
    slug = args.slug or ""
    mem_id = generate_id(mem_type, slug)

    now = datetime.datetime.now().strftime("%Y-%m-%d")

    meta = {
        "id": mem_id,
        "type": mem_type,
        "importance": importance,
        "created": now,
        "last_accessed": now,
        "access_count": 1,
        "tags": tags,
        "links": links,
        "superseded_by": None,
        "supersedes": None,
        "forgotten": False,
        "sensitive": sensitive,
        "source_url": getattr(args, "source_url", None),
        "source_file": getattr(args, "source_file", None),
    }

    # Add correction-specific fields
    if mem_type == "correction":
        meta["corrects"] = getattr(args, "corrects", None)
        meta["scope"] = getattr(args, "scope", []) or []
        meta["wrong_pattern"] = getattr(args, "wrong_pattern", None)
        meta["correct_pattern"] = getattr(args, "correct_pattern", None)

    # Determine tier and directory
    tier = get_tier(meta)
    mem_dir = get_memory_dir(mem_type, tier)

    # Handle sensitive memories — encrypt and save to vault
    if sensitive:
        mem_path = save_vault_memory(mem_id, meta, content)
        # Index with vault path
        _update_index_entry(mem_id, meta, mem_path)
    else:
        mem_path = get_memory_dir(mem_type, tier) / f"{mem_id}.md"
        save_memory(mem_path, meta, content)
        _update_index_entry(mem_id, meta, mem_path)

    # Update graph
    if links:
        _add_to_graph(mem_id, links)

    # Update MEMORY.md
    rebuild_memory_md()

    # Git commit
    git_commit(f"add: {mem_id}", [mem_path, INDEX_PATH, GRAPH_PATH, MEMORY_MD_PATH])

    print(f"Added memory: {mem_id}")
    print(f"  Type: {mem_type}, Importance: {importance}, Tier: {tier}")
    print(f"  Path: {mem_path}")

# ========================================
# Command: list
# ========================================

def cmd_list(args):
    """List memories."""
    tier_filter = args.tier
    tag_filter = args.tag
    sort_by = args.sort or "importance"
    show_forgotten = args.forgotten or False

    # Load vault index once for metadata-only access to encrypted memories
    vault_index = {}
    vault_index_path = VAULT_DIR / "_vault.json"
    if vault_index_path.exists():
        try:
            vault_index = json.loads(vault_index_path.read_text())
        except Exception:
            pass

    memories = []
    for p in find_all_memories(include_cold=True, include_forgotten=show_forgotten):
        try:
            # Vault .enc files: read metadata from index without decrypting
            if p.suffix == ".enc":
                entry = vault_index.get(p.stem, {})
                meta = {
                    "id": entry.get("id", p.stem),
                    "type": entry.get("type", "sensitive"),
                    "importance": 0.5,
                    "tags": entry.get("tags", []),
                    "sensitive": True,
                    "encrypted": True,
                }
                tier = "vault"
                if tier_filter and tier != tier_filter:
                    continue
                if tag_filter and tag_filter not in meta.get("tags", []):
                    continue
                memories.append({
                    "id": meta["id"],
                    "type": meta["type"],
                    "importance": meta["importance"],
                    "tier": tier,
                    "tags": meta["tags"],
                    "last_accessed": "",
                    "access_count": 0,
                    "path": str(p),
                    "summary": entry.get("summary", "[encrypted]")[:80].replace("\n", " "),
                })
                continue

            meta, body = load_memory(p)
            if meta.get("forgotten") and not show_forgotten:
                continue
            tier = get_tier(meta)
            if tier_filter and tier != tier_filter:
                continue
            if tag_filter and tag_filter not in meta.get("tags", []):
                continue
            memories.append({
                "id": meta.get("id", p.stem),
                "type": meta.get("type", "unknown"),
                "importance": meta.get("importance", 0.5),
                "tier": tier,
                "tags": meta.get("tags", []),
                "last_accessed": meta.get("last_accessed", ""),
                "access_count": meta.get("access_count", 0),
                "path": str(p),
                "summary": body[:80].replace("\n", " "),
            })
        except Exception as e:
            from meme.log import get_logger
            get_logger('meme').warning(f'Command error in {os.path.basename(path)}: {e}')
            continue

    # Sort
    if sort_by == "importance":
        memories.sort(key=lambda x: -x["importance"])
    elif sort_by == "recent":
        memories.sort(key=lambda x: x["last_accessed"], reverse=True)
    elif sort_by == "heat":
        memories.sort(key=lambda x: -x["access_count"])

    if not memories:
        if getattr(args, "format", "text") == "json":
            print(json.dumps([]))
        else:
            print("No memories found.")
        return

    if getattr(args, "format", "text") == "json":
        import datetime as _dt
        def _json_default(obj):
            if isinstance(obj, (_dt.date, _dt.datetime)):
                return obj.isoformat()
            raise TypeError
        print(json.dumps(memories, ensure_ascii=False, default=_json_default))
        return

    # Display
    for m in memories:
        tags_str = f" [{', '.join(m['tags'][:3])}]" if m['tags'] else ""
        print(f"  {m['id']}  imp={m['importance']:.1f}  tier={m['tier']}  "
              f"type={m['type']}{tags_str}")
        print(f"    {m['summary']}")
    print(f"\nTotal: {len(memories)} memories")

# ========================================
# Command: show
# ========================================

def cmd_show(args):
    """Show a memory's full content. For vault memories, triggers auth."""
    mem_id = args.id
    path = find_memory_by_id(mem_id)
    if not path:
        print(f"Memory not found: {mem_id}")
        return

    # Vault memory: decrypt (triggers Touch ID / password)
    if path.suffix == ".enc":
        try:
            meta, body = load_vault_memory(mem_id)
        except Exception as e:
            print(f"ERROR: Failed to decrypt vault memory — {e}", file=sys.stderr)
            sys.exit(1)
        print(f"ID: {meta.get('id', mem_id)}")
        print(f"Type: {meta.get('type', 'unknown')}")
        print(f"Importance: {meta.get('importance', 0.5)}")
        print(f"Tags: {', '.join(meta.get('tags', []))}")
        print(f"Tier: vault [encrypted]")
        print("-" * 40)
        print(body)
        return

    # Regular memory
    meta, body = load_memory(path)
    print(f"ID: {meta.get('id', mem_id)}")
    print(f"Type: {meta.get('type', 'unknown')}")
    print(f"Importance: {meta.get('importance', 0.5)}")
    print(f"Tags: {', '.join(meta.get('tags', []))}")
    print(f"Tier: {get_tier(meta)}")
    print("-" * 40)
    print(body)

# ========================================
# Command: search
# ========================================

def _extract_title(body: str, mem_id: str) -> str:
    """Extract title from body h1 or fallback to id."""
    m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return mem_id


def cmd_search(args):
    """Search memories by keyword (simple TF-IDF-like scoring)."""
    query = args.query.lower()
    query_terms = set(re.findall(r"\w+", query))

    results = []
    # Load vault index for searching encrypted memories without decryption
    vault_index = {}
    vault_index_path = VAULT_DIR / "_vault.json"
    if vault_index_path.exists():
        try:
            vault_index = json.loads(vault_index_path.read_text())
        except Exception:
            pass

    for p in find_all_memories(include_cold=True):
        try:
            # Vault .enc files: search against index summary (no decryption)
            if p.suffix == ".enc":
                vid = p.stem
                entry = vault_index.get(vid, {})
                text = (entry.get("summary", "") + " " + " ".join(entry.get("tags", []))).lower()
                score = sum(text.count(t) for t in query_terms)
                if score > 0:
                    results.append({
                        "id": vid,
                        "title": entry.get("summary", vid)[:60],
                        "type": entry.get("type", "sensitive"),
                        "importance": 0.5,
                        "tier": "vault",
                        "score": score,
                        "path": str(p),
                        "summary": entry.get("summary", "[encrypted]")[:120],
                        "content": entry.get("summary", "[encrypted]")[:500],
                        "tags": entry.get("tags", []),
                        "sensitive": True,
                    })
                continue

            meta, body = load_memory(p)
            if meta.get("forgotten"):
                continue
            text = (body + " " + " ".join(meta.get("tags", []))).lower()
            # Simple scoring: count term occurrences
            score = 0
            for term in query_terms:
                score += text.count(term)
            # Boost correction type
            if meta.get("type") == "correction":
                score *= 1.5
            if score > 0:
                tier = get_tier(meta)
                results.append({
                    "id": meta.get("id", p.stem),
                    "title": _extract_title(body, meta.get("id", p.stem)),
                    "type": meta.get("type", "unknown"),
                    "importance": meta.get("importance", 0.5),
                    "tier": tier,
                    "score": score,
                    "path": str(p),
                    "summary": body[:120].replace("\n", " "),
                    "content": body[:500].replace("\n", " "),
                    "tags": meta.get("tags", []),
                })
        except Exception as e:
            from meme.log import get_logger
            get_logger('meme').warning(f'Command error in {os.path.basename(path)}: {e}')
            continue

    results.sort(key=lambda x: -x["score"])

    if not results:
        if getattr(args, "format", "text") == "json":
            print("[]")
        else:
            print(f'No memories found for "{args.query}".')
        return

    if getattr(args, "format", "text") == "json":
        # JSON output for programmatic consumption (e.g., hooks)
        out = []
        for r in results[:20]:
            item = {
                "id": r["id"],
                "title": r["title"],
                "importance": r["importance"],
                "tier": r["tier"],
                "tags": r.get("tags", []),
            }
            # Vault memories: do not leak content, return masked summary only
            if r.get("sensitive") or r["tier"] == "vault":
                item["content"] = r.get("summary", "[encrypted]")
                item["sensitive"] = True
            else:
                item["content"] = r.get("content", r["summary"])
            out.append(item)
        print(json.dumps(out))
        return

    # Text output (default)
    print(f'Search results for "{args.query}":\n')
    for r in results[:20]:
        cold_marker = " [cold] ⚠️ 较久未使用" if r["tier"] == "cold" else ""
        print(f"  [{r['tier']}] {r['id']} (importance: {r['importance']:.1f}, "
              f"score: {r['score']:.0f}){cold_marker}")
        print(f"    {r['summary']}")
    print(f"\nFound: {len(results)} memories (showing top {min(20, len(results))})")

# ========================================
# Command: query (graph traversal)
# ========================================

def cmd_query(args):
    """Graph traversal retrieval from a memory node."""
    mem_id = args.id
    graph = load_graph()

    # BFS traversal
    visited = {}
    queue = deque([(mem_id, 0)])
    visited[mem_id] = 0

    while queue:
        current, dist = queue.popleft()
        if dist >= 3:
            continue
        neighbors = graph.get(current, [])
        for neighbor in neighbors:
            if neighbor not in visited:
                visited[neighbor] = dist + 1
                queue.append((neighbor, dist + 1))

    # Load and display results by distance
    for dist in range(3):
        nodes_at_dist = [(mid, d) for mid, d in visited.items() if d == dist]
        if not nodes_at_dist:
            continue

        label = {0: "命中节点", 1: "1 级连接", 2: "2 级连接"}[dist]
        print(f"\n{'='*40}")
        print(f"  {label} (distance={dist})")
        print(f"{'='*40}")

        for mid, _ in sorted(nodes_at_dist):
            path = find_memory_by_id(mid)
            if not path:
                continue
            try:
                meta, body = load_memory(path)
                imp = meta.get("importance", 0.5)
                load_w = imp * (0.4 ** dist)

                if dist == 0:
                    # Full load
                    print(f"\n  [{mid}] importance={imp:.1f}")
                    print(f"  {body}")
                elif dist == 1:
                    # Full load (likely passes threshold)
                    print(f"\n  [{mid}] importance={imp:.1f}, load_weight={load_w:.2f}")
                    print(f"  {body[:200]}")
                else:
                    # Title + description only
                    print(f"\n  [{mid}] importance={imp:.1f}, load_weight={load_w:.2f}")
                    print(f"  Tags: {', '.join(meta.get('tags', []))}")
            except Exception:
                continue

    # Update access
    path = find_memory_by_id(mem_id)
    if path:
        meta, body = load_memory(path)
        meta["last_accessed"] = datetime.datetime.now().strftime("%Y-%m-%d")
        meta["access_count"] = meta.get("access_count", 0) + 1
        save_memory(path, meta, body)
        _update_index_entry(mem_id, meta, path)

# ========================================
# Command: edit
# ========================================

def cmd_edit(args):
    """Edit a memory."""
    mem_id = args.id
    path = find_memory_by_id(mem_id)
    if not path:
        print(f"Memory not found: {mem_id}")
        return

    meta, body = load_memory(path)

    if args.importance is not None:
        meta["importance"] = args.importance
    if args.type:
        meta["type"] = args.type
    if args.tags:
        meta["tags"] = [t.strip() for t in args.tags.split(",")]
    if args.content:
        body = args.content
    if args.add_link:
        links = meta.get("links", [])
        for link in args.add_link.split(","):
            link = link.strip()
            if link and link not in links:
                links.append(link)
        meta["links"] = links
        _add_to_graph(mem_id, links)

    meta["last_accessed"] = datetime.datetime.now().strftime("%Y-%m-%d")
    save_memory(path, meta, body)
    _update_index_entry(mem_id, meta, path)
    rebuild_memory_md()
    git_commit(f"edit: {mem_id}", [path, INDEX_PATH, GRAPH_PATH, MEMORY_MD_PATH])
    print(f"Updated: {mem_id}")

# ========================================
# Command: delete
# ========================================

def cmd_delete(args):
    """Delete a memory."""
    mem_id = args.id
    path = find_memory_by_id(mem_id)
    if not path:
        print(f"Memory not found: {mem_id}")
        return

    if not args.force:
        confirm = input(f"Delete {mem_id}? [y/N] ")
        if confirm.lower() != "y":
            print("Cancelled.")
            return

    path.unlink()
    _remove_from_index(mem_id)
    _remove_from_graph(mem_id)
    rebuild_memory_md()
    git_commit(f"delete: {mem_id}", [INDEX_PATH, GRAPH_PATH, MEMORY_MD_PATH])
    print(f"Deleted: {mem_id}")

# ========================================
# Command: forget
# ========================================

def cmd_forget(args):
    """Forget a memory (soft/hard/purge)."""
    mem_id = args.id
    path = find_memory_by_id(mem_id)
    if not path:
        print(f"Memory not found: {mem_id}")
        return

    meta, body = load_memory(path)

    if args.hard:
        # Hard forget: delete file + index
        if args.purge:
            # Purge from git history too
            try:
                git_run("filter-branch", "--force", "--index-filter",
                        f"git rm --cached --ignore-unmatch {path}", "--prune-empty",
                        "--", "--all")
            except subprocess.CalledProcessError:
                pass
        path.unlink()
        _remove_from_index(mem_id)
        _remove_from_graph(mem_id)
        print(f"Hard-forgotten: {mem_id}")
    else:
        # Soft forget: mark as forgotten
        meta["forgotten"] = True
        meta["forgotten_at"] = datetime.datetime.now().isoformat()
        meta["forgotten_reason"] = args.reason or "User requested"
        save_memory(path, meta, body)

        # Move to forgotten index
        forgotten = load_forgotten_index()
        forgotten[mem_id] = {
            "forgotten_at": meta["forgotten_at"],
            "reason": meta["forgotten_reason"],
        }
        save_forgotten_index(forgotten)
        print(f"Soft-forgotten: {mem_id}")

    rebuild_memory_md()
    git_commit(f"forget: {mem_id}")

# ========================================
# Command: learn
# ========================================

def cmd_learn(args):
    """Learn from URL or file and create a memory."""
    import requests as req

    url = args.url or args.url_flag
    if url:
        print(f"Fetching: {url}")
        try:
            resp = req.get(url, timeout=30)
            resp.raise_for_status()
            content = resp.text
            # Strip HTML tags for a rough text extraction
            content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
            content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()
            source_url = url
        except Exception as e:
            print(f"Failed to fetch URL: {e}")
            return
    elif args.file:
        file_path = Path(args.file).expanduser()
        if not file_path.exists():
            print(f"File not found: {file_path}")
            return
        content = file_path.read_text(encoding="utf-8")
        source_url = None
    else:
        print("Provide --url or --file")
        return

    # Truncate if too long
    if len(content) > 10000:
        content = content[:10000] + "\n\n[Content truncated]"

    # Create memory
    slug = args.slug or ""
    mem_id = generate_id("knowledge", slug)
    now = datetime.datetime.now().strftime("%Y-%m-%d")
    importance = args.importance or 0.5
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]

    # Auto-extract tags from content keywords
    if not tags:
        words = re.findall(r"\b[a-zA-Z]{4,}\b", content.lower())
        word_freq = {}
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1
        tags = sorted(word_freq, key=word_freq.get, reverse=True)[:5]

    meta = {
        "id": mem_id,
        "type": "knowledge",
        "importance": importance,
        "created": now,
        "last_accessed": now,
        "access_count": 1,
        "tags": tags,
        "links": [],
        "superseded_by": None,
        "forgotten": False,
        "sensitive": False,
        "source_url": source_url,
        "source_file": str(args.file) if args.file else None,
    }

    # Find related existing memories for auto-linking
    related = _find_related(content, tags)
    if related:
        meta["links"] = related
        _add_to_graph(mem_id, related)

    # Save
    tier = get_tier(meta)
    mem_dir = get_memory_dir("knowledge", tier)
    mem_path = mem_dir / f"{mem_id}.md"

    # Truncate body for storage
    body = content[:5000] if len(content) > 5000 else content
    save_memory(mem_path, meta, body)
    _update_index_entry(mem_id, meta, mem_path)
    rebuild_memory_md()
    git_commit(f"learn: {mem_id}", [mem_path, INDEX_PATH, GRAPH_PATH, MEMORY_MD_PATH])

    print(f"Learned: {mem_id}")
    print(f"  Tags: {', '.join(tags)}")
    if related:
        print(f"  Auto-linked to: {', '.join(related)}")


def _find_related(content: str, tags: list[str]) -> list[str]:
    """Find related memories based on keyword overlap."""
    content_lower = content.lower()
    related = []
    for p in find_all_memories():
        try:
            meta, body = load_memory(p)
            if meta.get("forgotten"):
                continue
            # Check tag overlap
            mem_tags = set(meta.get("tags", []))
            overlap = mem_tags.intersection(set(tags))
            if len(overlap) >= 2:
                related.append(meta["id"])
                continue
            # Check keyword overlap
            mem_words = set(re.findall(r"\b[a-z]{4,}\b", body.lower()))
            content_words = set(re.findall(r"\b[a-z]{4,}\b", content_lower))
            common = mem_words.intersection(content_words)
            if len(common) >= 5:
                related.append(meta["id"])
        except Exception as e:
            from meme.log import get_logger
            get_logger('meme').warning(f'Command error in {os.path.basename(path)}: {e}')
            continue
    return related[:10]  # Cap at 10 links

