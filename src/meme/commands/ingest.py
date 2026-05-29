"""Ingestion commands."""

import datetime
import os
import re
from pathlib import Path

from meme.constants import (
    WORKING_DIR,
)
from meme.utils import (
    _add_to_graph,
    _update_index_entry,
    create_memory_record,
    find_all_memories,
    find_memory_by_id,
    generate_id,
    get_memory_dir,
    get_tier,
    git_commit,
    load_memory,
    parse_frontmatter,
    rebuild_memory_md,
    save_memory,
)

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
        word_freq: dict[str, int] = {}
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1
        tags = sorted(word_freq, key=lambda k: word_freq[k], reverse=True)[:5]

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

    # Truncate body for storage
    body = content[:5000] if len(content) > 5000 else content
    create_memory_record(meta, body)

    print(f"Learned: {mem_id}")
    print(f"  Tags: {', '.join(tags)}")
    if related:
        print(f"  Auto-linked to: {', '.join(related)}")


def _find_related(content: str, tags: list[str]) -> list[str]:
    """Find related memories based on keyword overlap."""
    content_lower = content.lower()
    related = []
    for p in find_all_memories():
        if p.suffix == ".enc":
            continue
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

            get_logger("meme").warning(f"Command error in {os.path.basename(p)}: {e}")
            continue
    return related[:10]  # Cap at 10 links


# ========================================
# Command: import
# ========================================


def cmd_import(args):
    """Import memories from external sources."""
    sources = args.source  # list of sources
    dry_run = getattr(args, "dry_run", False)
    for src in sources:
        if src == "claude":
            _do_import_claude(dry_run=dry_run)
        elif src == "claude-global":
            _do_import_claude_global()
        elif src == "codex":
            _do_import_codex(getattr(args, "path", None))
        else:
            print(f"Unknown source: {src}")


def _do_import_claude(dry_run: bool = False):
    """Import from Claude Code project memories."""
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        print("No Claude Code projects found.")
        return

    imports = []  # [(meta, body, project_name, fname, mem_path), ...]
    imported_ids: dict[str, list[str]] = {}

    for proj_dir in claude_projects.iterdir():
        if not proj_dir.is_dir():
            continue
        memory_dir = proj_dir / "memory"
        if not memory_dir.exists():
            continue

        project_name = proj_dir.name.lstrip("-").replace("-", "_")
        parts = project_name.split("_")
        project_name = parts[-1] if parts else project_name

        for md_file in memory_dir.glob("*.md"):
            if md_file.name == "MEMORY.md":
                continue
            try:
                text = md_file.read_text(encoding="utf-8")
                meta, body = parse_frontmatter(text)

                fname = md_file.stem
                if fname.startswith("feedback_"):
                    mem_type = "feedback"
                elif fname.startswith("project_"):
                    mem_type = "project"
                elif fname.startswith("user_"):
                    mem_type = "user"
                elif fname.startswith("reference_"):
                    mem_type = "reference"
                else:
                    mem_type = "feedback"

                if not meta.get("id"):
                    meta["id"] = generate_id(mem_type, fname)
                if not meta.get("type"):
                    meta["type"] = mem_type
                if not meta.get("importance"):
                    meta["importance"] = 0.6
                if not meta.get("created"):
                    meta["created"] = datetime.date.today().strftime("%Y-%m-%d")
                if not meta.get("last_accessed"):
                    meta["last_accessed"] = meta["created"]
                if not meta.get("access_count"):
                    meta["access_count"] = 1
                if not meta.get("tags"):
                    meta["tags"] = [project_name]
                elif project_name not in meta["tags"]:
                    meta["tags"].append(project_name)

                if find_memory_by_id(meta["id"]):
                    continue

                tier = get_tier(meta)
                mem_dir = get_memory_dir(mem_type, tier)
                mem_path = mem_dir / f"{meta['id']}.md"

                imported_ids.setdefault(project_name, []).append(meta["id"])
                imports.append((meta, body, project_name, fname, mem_path))
            except Exception as e:
                print(f"  Failed to import {md_file}: {e}")

    if dry_run:
        for meta, _body, project_name, fname, mem_path in imports:
            peers = [mid for mid in imported_ids[project_name] if mid != meta["id"]]
            print(f"  [dry-run] Would import {fname} -> {mem_path}")
            if peers:
                print(f"            Links: {', '.join(peers)}")
        print(f"Would import {len(imports)} memories from Claude Code projects.")
        return

    imported = 0
    for meta, body, project_name, _fname, _mem_path in imports:
        peers = [mid for mid in imported_ids[project_name] if mid != meta["id"]]
        existing = meta.get("links", [])
        meta["links"] = list(set(existing + peers))
        create_memory_record(meta, body)
        imported += 1

    if imported:
        print(f"Imported {imported} memories from Claude Code projects.")


def _do_import_claude_global():
    """Import from Claude Code global CLAUDE.md."""
    claude_md = Path.home() / ".claude" / "CLAUDE.md"
    if not claude_md.exists():
        print("No global CLAUDE.md found.")
        return

    text = claude_md.read_text(encoding="utf-8")
    # Split into sections
    sections = re.split(r"\n## ", text)

    imported = 0
    for section in sections:
        if not section.strip():
            continue
        lines = section.strip().split("\n")
        title = lines[0].strip().replace("#", "").strip()
        body = "\n".join(lines[1:]).strip()
        if not body:
            continue

        slug = re.sub(r"[^a-z0-9]+", "_", title.lower())[:30]
        mem_id = generate_id("user", slug)

        # Check duplicate
        if find_memory_by_id(mem_id):
            continue

        meta = {
            "id": mem_id,
            "type": "user",
            "importance": 0.8,  # Global config = high importance
            "created": datetime.date.today().strftime("%Y-%m-%d"),
            "last_accessed": datetime.date.today().strftime("%Y-%m-%d"),
            "access_count": 1,
            "tags": ["global-config"],
            "links": [],
            "forgotten": False,
            "sensitive": False,
        }

        mem_path = WORKING_DIR / f"{mem_id}.md"
        save_memory(mem_path, meta, body)
        _update_index_entry(mem_id, meta, mem_path)
        imported += 1

    if imported:
        rebuild_memory_md()
        git_commit(f"import: {imported} memories from Claude global config")
    print(f"Imported {imported} memories from Claude global config.")


def _do_import_codex(codex_path: str | None):
    """Import from Codex workspace."""
    if codex_path:
        base = Path(codex_path).expanduser()
    else:
        base = Path.home() / "Documents" / "Meme"
    if not base.exists():
        print(f"Codex path not found: {base}")
        return
    print(f"Importing from Codex at {base}...")
    # Codex import: scan for memory files
    imported = 0
    for md_file in base.rglob("*.md"):
        if ".meme" in str(md_file):
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(text)
            if not meta.get("id"):
                meta["id"] = generate_id("knowledge", md_file.stem)
            if not meta.get("type"):
                meta["type"] = "knowledge"
            if not meta.get("importance"):
                meta["importance"] = 0.5

            existing = find_memory_by_id(meta["id"])
            if existing:
                continue

            tier = get_tier(meta)
            mem_dir = get_memory_dir("knowledge", tier)
            mem_path = mem_dir / f"{meta['id']}.md"
            save_memory(mem_path, meta, body)
            _update_index_entry(meta["id"], meta, mem_path)
            imported += 1
        except Exception as e:
            from meme.log import get_logger

            get_logger("meme").warning(f"Command error in {os.path.basename(md_file)}: {e}")
            continue

    if imported:
        rebuild_memory_md()
        git_commit(f"import: {imported} memories from Codex")
    print(f"Imported {imported} memories from Codex.")
