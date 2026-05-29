"""Link and consolidation commands."""
import datetime
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from meme.constants import (
    MEME_HOME, META_DIR, WORKING_DIR, ARCHIVE_DIR, COLD_DIR, CONFIG_PATH,
)
from meme.config import load_config, save_config, get_config_value, set_config_value
from meme.utils import (
    load_memory, save_memory, find_all_memories, find_memory_by_id,
    load_graph, save_graph, _add_to_graph, rebuild_memory_md, git_commit,
)

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


def cmd_suggest_links(args):
    """Suggest new links based on heat patterns and content similarity."""
    suggestions = []
    memories = []
    for p in find_all_memories():
        if p.suffix == ".enc":
            continue
        try:
            meta, body = load_memory(p)
            memories.append((meta, body))
        except Exception:
            continue

    for i, (meta_a, body_a) in enumerate(memories):
        for meta_b, body_b in memories[i+1:]:
            if meta_a.get("id") == meta_b.get("id"):
                continue
            # Check if already linked
            existing_links = set(meta_a.get("links", []))
            if meta_b["id"] in existing_links:
                continue
            # Check content similarity
            words_a = set(re.findall(r"\b[a-z]{4,}\b", body_a.lower()))
            words_b = set(re.findall(r"\b[a-z]{4,}\b", body_b.lower()))
            common = words_a.intersection(words_b)
            if len(common) >= 3:
                suggestions.append({
                    "a": meta_a["id"],
                    "b": meta_b["id"],
                    "common_words": len(common),
                    "sample": list(common)[:5],
                })

    suggestions.sort(key=lambda x: -x["common_words"])

    if not suggestions:
        print("No link suggestions found.")
        return

    print("Suggested links:\n")
    for s in suggestions[:20]:
        print(f"  {s['a']} <-> {s['b']}  (common words: {s['common_words']})")
        print(f"    Sample: {', '.join(s['sample'])}")

# ========================================
# Command: daydream
# ========================================

_DAYDREAM_STOPS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it",
    "they", "them", "this", "that", "these", "those",
    "and", "or", "but", "if", "then", "else", "when", "at", "by", "for",
    "with", "about", "against", "between", "through", "during", "before",
    "after", "above", "below", "to", "from", "up", "down", "in", "out",
    "on", "off", "over", "under", "again", "further", "than", "once",
    "here", "there", "why", "how", "all", "each", "every", "both", "few",
    "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "so", "very", "just", "because", "as", "until", "while",
    "of", "into", "what", "which", "who", "whom", "whose",
    "help", "please", "want", "know", "think", "make", "get", "go", "come",
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个", "上", "也",
    "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好", "自己", "这", "那",
    "使用", "进行", "通过", "需要", "可以", "应该", "我们", "他们", "这个", "那个",
    "这些", "那些", "什么", "怎么", "为什么", "哪里", "时候", "现在", "然后", "但是",
    "因为", "所以", "如果", "虽然", "已经", "正在", "将要", "好的", "是的", "对的", "请",
    "帮", "他", "她", "它", "吗", "呢", "吧", "给", "把", "被", "让", "对", "向", "从",
}


def _extract_significant_words(text: str) -> set[str]:
    """Extract significant words from memory content."""
    if not text:
        return set()
    text = text.lower()
    words = set(re.findall(r"\b[a-z]{3,}\b", text))
    chinese = re.findall(r"[一-鿿]{2,}", text)
    words.update(chinese)
    return words - _DAYDREAM_STOPS


def _memory_similarity(m1: dict, m2: dict) -> float:
    """Compute composite similarity between two memory dicts."""
    score = 0.0
    if m1.get("type") == m2.get("type"):
        score += 0.1
    tags1 = set(m1.get("tags", []))
    tags2 = set(m2.get("tags", []))
    if tags1 or tags2:
        union = tags1 | tags2
        if union:
            score += len(tags1 & tags2) / len(union) * 0.25
    words1 = _extract_significant_words(m1.get("body", ""))
    words2 = _extract_significant_words(m2.get("body", ""))
    if words1 or words2:
        union = words1 | words2
        if union:
            score += len(words1 & words2) / len(union) * 0.45
    id1 = m1.get("id", "")
    id2 = m2.get("id", "")
    if id1 and id2 and (id1 in m2.get("body", "") or id2 in m1.get("body", "")):
        score += 0.2
    return min(score, 1.0)


def _daydream_cluster(memories: list[dict], threshold: float) -> list[list[dict]]:
    """Cluster memories with union-find."""
    n = len(memories)
    if n < 2:
        return []
    parent = list(range(n))
    rank = [0] * n

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int):
        px, py = find(x), find(y)
        if px == py:
            return
        if rank[px] < rank[py]:
            px, py = py, px
        parent[py] = px
        if rank[px] == rank[py]:
            rank[px] += 1

    for i in range(n):
        for j in range(i + 1, n):
            if _memory_similarity(memories[i], memories[j]) >= threshold:
                union(i, j)

    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(memories[i])
    return [members for members in clusters.values() if len(members) > 1]


def _cluster_keywords(cluster: list[dict]) -> list[str]:
    """Extract top keywords for a cluster."""
    all_words = []
    for m in cluster:
        all_words.extend(_extract_significant_words(m.get("body", "")))
    if not all_words:
        return []
    from collections import Counter
    return [w for w, _ in Counter(all_words).most_common(5)]


def _daydream_report(memories: list[dict], clusters: list[list[dict]],
                     link_suggestions: list[dict], dry_run: bool):
    """Print consolidation report."""
    clustered_ids = {m["id"] for c in clusters for m in c}
    orphans = [m for m in memories if m["id"] not in clustered_ids]

    print("=" * 60)
    print("Daydream Report")
    print("=" * 60)
    print()

    if clusters:
        print(f"Found {len(clusters)} semantic cluster(s):")
        for i, cluster in enumerate(clusters, 1):
            keywords = _cluster_keywords(cluster)
            print(f"\n  Cluster {i}: {', '.join(keywords) if keywords else '(no keywords)'}")
            print(f"  {'─' * 50}")
            for m in cluster:
                tags = ", ".join(m.get("tags", [])) or "none"
                body = m.get("body", "").replace("\n", " ")[:60]
                print(f"    • {m['id']} ({m.get('type', '?')}) [tags: {tags}]")
                print(f"      {body}...")
    else:
        print("No semantic clusters found.")

    if link_suggestions:
        print(f"\nSuggested {len(link_suggestions)} new link(s):")
        for s in link_suggestions:
            print(f"  {s['a']} <-> {s['b']}")
            reason = s.get("reason", "")
            if reason:
                print(f"    reason: {reason}")
    else:
        print("\nNo new link suggestions.")

    if orphans:
        print(f"\n{len(orphans)} isolated memory/ies:")
        for m in orphans[:10]:
            print(f"  • {m['id']}")
        if len(orphans) > 10:
            print(f"    ... and {len(orphans) - 10} more")

    if dry_run:
        print("\n" + "─" * 60)
        print("Dry run — no changes applied.")
        print("Run without --dry-run to apply suggestions.")
        print("─" * 60)


def cmd_daydream(args):
    """Daydream: semantic clustering and link consolidation."""
    config = load_config()
    dd_cfg = config.get("daydream", {})
    dry_run = getattr(args, "dry_run", False)
    mode = getattr(args, "mode", None) or getattr(dd_cfg, "default_mode", "all")
    threshold = getattr(args, "threshold", None)
    if threshold is None:
        threshold = getattr(dd_cfg, "threshold", 0.4)
    apply_links = getattr(args, "apply", False) or getattr(dd_cfg, "auto_apply", False)
    merge = getattr(args, "merge", False) or getattr(dd_cfg, "merge", False)

    print(f"Daydream — memory consolidation")
    print(f"  mode: {mode}, threshold: {threshold}, dry_run: {dry_run}")
    print()

    memories = []
    for p in find_all_memories(include_cold=True):
        if p.suffix == ".enc":
            continue
        try:
            meta, body = load_memory(p)
            if meta.get("forgotten"):
                continue
            memories.append({
                "path": p,
                "meta": meta,
                "body": body,
                "id": meta.get("id", p.stem),
                "type": meta.get("type", "feedback"),
                "tags": list(meta.get("tags", [])),
                "links": set(meta.get("links", [])),
            })
        except Exception:
            continue

    if not memories:
        print("No memories found to consolidate.")
        return

    print(f"Loaded {len(memories)} memories\n")

    # Phase 1: Cluster
    clusters = []
    if mode in ("all", "cluster"):
        clusters = _daydream_cluster(memories, threshold)

    # Save cluster info for semantic retrieval in hooks
    if clusters:
        cluster_data = {
            "generated_at": datetime.datetime.now().isoformat(),
            "clusters": [],
        }
        for cluster in clusters:
            keywords = _cluster_keywords(cluster)
            core = max(cluster, key=lambda m: m["meta"].get("importance", 0.5))
            cluster_data["clusters"].append({
                "keywords": keywords,
                "core_id": core["id"],
                "members": [m["id"] for m in cluster],
            })
        clusters_path = Path(MEME_HOME) / "meta" / "clusters.json"
        clusters_path.write_text(json.dumps(cluster_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Phase 2: Links
    link_suggestions = []
    seen_pairs = set()
    if mode in ("all", "link"):
        for cluster in clusters:
            for i, m1 in enumerate(cluster):
                for m2 in cluster[i + 1:]:
                    pair = tuple(sorted([m1["id"], m2["id"]]))
                    if m2["id"] not in m1["links"] and m1["id"] not in m2["links"]:
                        if pair not in seen_pairs:
                            seen_pairs.add(pair)
                            link_suggestions.append({
                                "a": m1["id"],
                                "b": m2["id"],
                                "reason": f"cluster: {_cluster_keywords(cluster)[:3]}",
                            })

        for i, m1 in enumerate(memories):
            for m2 in memories[i + 1:]:
                if m1["id"] in m2.get("body", "") or m2["id"] in m1.get("body", ""):
                    pair = tuple(sorted([m1["id"], m2["id"]]))
                    if pair not in seen_pairs:
                        if m2["id"] not in m1["links"] and m1["id"] not in m2["links"]:
                            seen_pairs.add(pair)
                            link_suggestions.append({
                                "a": m1["id"],
                                "b": m2["id"],
                                "reason": "explicit cross-reference",
                            })

    _daydream_report(memories, clusters, link_suggestions, dry_run)

    # Apply links
    applied = 0
    if not dry_run and apply_links and link_suggestions:
        for s in link_suggestions:
            path_a = find_memory_by_id(s["a"])
            path_b = find_memory_by_id(s["b"])
            if not path_a or not path_b:
                continue
            try:
                meta_a, body_a = load_memory(path_a)
                meta_b, body_b = load_memory(path_b)
                links_a = set(meta_a.get("links", []))
                links_b = set(meta_b.get("links", []))
                changed = False
                if s["b"] not in links_a:
                    links_a.add(s["b"])
                    meta_a["links"] = sorted(links_a)
                    save_memory(path_a, meta_a, body_a)
                    changed = True
                if s["a"] not in links_b:
                    links_b.add(s["a"])
                    meta_b["links"] = sorted(links_b)
                    save_memory(path_b, meta_b, body_b)
                    changed = True
                if changed:
                    applied += 1
            except Exception:
                continue
        if applied:
            print(f"\nApplied {applied} new link(s).")

    # Merge duplicate memories within clusters
    merged = 0
    if not dry_run and merge and clusters:
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            # Pick highest-importance memory as core
            core = max(cluster, key=lambda m: m["meta"].get("importance", 0.5))
            core_path = core["path"]
            core_meta, core_body = load_memory(core_path)

            # Collect merged content from others
            merged_lines = []
            merged_tags = set(core_meta.get("tags", []))
            merged_links = set(core_meta.get("links", []))

            for m in cluster:
                if m["id"] == core["id"]:
                    continue
                try:
                    other_meta, other_body = load_memory(m["path"])
                    merged_lines.append(f"\n\n<!-- merged from {m['id']} -->")
                    merged_lines.append(other_body.strip())
                    merged_tags.update(other_meta.get("tags", []))
                    merged_links.update(other_meta.get("links", []))
                    # Remove merged memory
                    m["path"].unlink()
                    # Remove from index
                    from meme.utils import _remove_from_index
                    _remove_from_index(m["id"])
                except Exception:
                    continue

            if merged_lines:
                new_body = core_body.strip() + "\n".join(merged_lines)
                core_meta["tags"] = sorted(merged_tags)
                core_meta["links"] = sorted(merged_links)
                core_meta["last_accessed"] = datetime.date.today().strftime("%Y-%m-%d")
                save_memory(core_path, core_meta, new_body)
                merged += len(merged_lines) // 2  # Each memory contributes 2 lines

        if merged:
            print(f"\nMerged {merged} duplicate memory/ies into core memories.")

    # Sync graph and index
    if not dry_run and (clusters or link_suggestions or merged):
        graph = {}
        for p in find_all_memories(include_cold=True):
            if p.suffix == ".enc":
                continue
            try:
                meta, _ = load_memory(p)
                mem_id = meta.get("id")
                if mem_id:
                    graph[mem_id] = sorted(set(meta.get("links", [])))
            except Exception:
                continue
        save_graph(graph)
        rebuild_memory_md()
        git_commit("daydream: consolidated memory graph")

    print("\nDaydream complete.")


# ========================================
# Command: config
# ========================================


def cmd_config(args):
    """View or modify Meme configuration."""
    config = load_config()
    get_path = getattr(args, "get", None)
    set_path = getattr(args, "set", None)
    edit = getattr(args, "edit", False)

    if get_path:
        val = get_config_value(config, get_path)
        if val is None:
            print(f"Config key not found: {get_path}")
            sys.exit(1)
        print(val)
        return

    if set_path:
        # Parse "key=value"
        if "=" not in set_path:
            print("Usage: meme config --set key=value")
            sys.exit(1)
        key_path, value = set_path.split("=", 1)
        key_path = key_path.strip()
        value = value.strip()
        if set_config_value(config, key_path, value):
            save_config(config)
            print(f"Set {key_path} = {get_config_value(config, key_path)}")
        else:
            print(f"Failed to set {key_path}")
            sys.exit(1)
        return

    if edit:
        editor = os.environ.get("EDITOR", "vi")
        if not CONFIG_PATH.exists():
            save_config(config)
        subprocess.run([editor, str(CONFIG_PATH)])
        return

    # Default: print full config
    print("# Meme Configuration")
    print(f"# Source: {CONFIG_PATH}")
    print()
    for section, values in config.items():
        print(f"[{section}]")
        for key, val in values.items():
            if isinstance(val, bool):
                print(f"  {key} = {str(val).lower()}")
            elif isinstance(val, str):
                print(f'  {key} = "{val}"')
            else:
                print(f"  {key} = {val}")
        print()


# ========================================
# Command: dream
# ========================================


def _cron_to_launchd_dict(schedule: str) -> dict:
    """Convert a 5-field cron expression to launchd StartCalendarInterval keys."""
    parts = schedule.split()
    if len(parts) != 5:
        return {"Hour": 3, "Minute": 0}
    minute, hour, day, month, weekday = parts
    result = {}

    def _parse_field(field: str, key: str, rng: tuple):
        if field == "*":
            return
        if "," in field:
            vals = []
            for p in field.split(","):
                if "-" in p:
                    start, end = p.split("-", 1)
                    vals.extend(range(int(start), int(end) + 1))
                elif "/" in p:
                    base, step = p.split("/", 1)
                    start = int(base) if base != "*" else rng[0]
                    vals.extend(range(start, rng[1] + 1, int(step)))
                else:
                    vals.append(int(p))
            result[key] = vals[0] if len(vals) == 1 else vals
            return
        if "-" in field:
            start, end = field.split("-", 1)
            result[key] = list(range(int(start), int(end) + 1))
            return
        if "/" in field:
            base, step = field.split("/", 1)
            start = int(base) if base != "*" else rng[0]
            result[key] = list(range(start, rng[1] + 1, int(step)))
            return
        result[key] = int(field)

    _parse_field(minute, "Minute", (0, 59))
    _parse_field(hour, "Hour", (0, 23))
    _parse_field(day, "Day", (1, 31))
    _parse_field(month, "Month", (1, 12))
    # Weekday: cron 0=Sun, launchd 0=Sun too
    _parse_field(weekday, "Weekday", (0, 7))
    return result


def _generate_launchd_plist(schedule: str) -> str:
    """Generate a launchd plist for the dream cron job."""
    interval = _cron_to_launchd_dict(schedule)
    meme_bin = str(MEME_HOME / "bin" / "meme")
    report_dir = str(MEME_HOME / "dreams")
    out_log = f"{report_dir}/dream.log"
    err_log = f"{report_dir}/dream.error.log"

    interval_xml = ""
    for key, val in interval.items():
        if isinstance(val, list):
            interval_xml += f"        <key>{key}</key>\n"
            interval_xml += "        <array>\n"
            for v in val:
                interval_xml += f"          <integer>{v}</integer>\n"
            interval_xml += "        </array>\n"
        else:
            interval_xml += f"        <key>{key}</key>\n"
            interval_xml += f"        <integer>{val}</integer>\n"

    plist = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.meme.dream</string>
    <key>ProgramArguments</key>
    <array>
        <string>{meme_bin}</string>
        <string>dream</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
{interval_xml}    </dict>
    <key>StandardOutPath</key>
    <string>{out_log}</string>
    <key>StandardErrorPath</key>
    <string>{err_log}</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>'''
    return plist


def cmd_dream(args):
    """Dream: automated nightly memory consolidation."""
    config = load_config()
    dream_cfg = config.get("dream", {})

    if not dream_cfg.get("enabled", True):
        print("Dream mode is disabled. Enable with: meme config --set dream.enabled=true")
        return

    threshold = dream_cfg.get("threshold", 0.4)
    mode = dream_cfg.get("mode", "all")
    auto_apply = dream_cfg.get("auto_apply", True)
    report_dir_name = dream_cfg.get("report_dir", "dreams")
    report_dir = MEME_HOME / report_dir_name
    report_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.date.today().strftime("%Y-%m-%d")
    report_path = report_dir / f"{today}.md"

    # Redirect output to report file
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()

    # Reuse daydream logic
    class FakeArgs:
        pass

    fake = FakeArgs()
    fake.dry_run = False
    fake.mode = mode
    fake.threshold = threshold
    fake.apply = auto_apply

    try:
        cmd_daydream(fake)
    except Exception as e:
        print(f"\nDream error: {e}")

    output = buffer.getvalue()
    sys.stdout = old_stdout

    # Write report
    report_lines = [
        f"# Dream Report — {today}",
        "",
        f"- Schedule: {dream_cfg.get('schedule', '0 3 * * *')}",
        f"- Threshold: {threshold}",
        f"- Mode: {mode}",
        f"- Auto-apply: {auto_apply}",
        "",
        "```",
    ]
    report_lines.extend(output.splitlines())
    report_lines.append("```")
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    # Record last run
    last_dream_path = META_DIR / "last_dream.txt"
    last_dream_path.write_text(today, encoding="utf-8")

    print(f"Dream complete. Report: {report_path}")


