#!/usr/bin/env bash
# ========================================
# Meme UserPromptSubmit Hook
# ========================================
# Extracts keywords from user prompt, searches memories,
# outputs related memories as additionalContext

set -euo pipefail

MEME_HOME="${MEME_HOME:-$HOME/.meme}"
MEME_BIN="$MEME_HOME/bin/meme"

# If meme CLI not installed, silently skip
if [[ ! -x "$MEME_BIN" ]]; then
    echo '{"continue":true,"suppressOutput":true}'
    exit 0
fi

# Read user prompt from stdin (Claude Code sends JSON)
INPUT=$(cat)

# Extract the prompt text from the JSON input
PROMPT=$(echo "$INPUT" | python3 -c '
import sys, json
try:
    data = json.load(sys.stdin)
    prompt = data.get("prompt", "")
    if isinstance(prompt, list):
        # Handle content blocks
        texts = [p.get("text","") for p in prompt if isinstance(p, dict) and p.get("type") == "text"]
        prompt = " ".join(texts)
    print(prompt)
except:
    print("")
' 2>/dev/null)

# Skip if prompt is empty or too short
if [[ -z "$PROMPT" || ${#PROMPT} -lt 3 ]]; then
    echo '{"continue":true,"suppressOutput":true}'
    exit 0
fi

# ---- Auto-add memory on "remember" commands ----
REMEMBER_CONTENT=$(echo "$PROMPT" | python3 -c '
import sys, re
text = sys.stdin.read().strip()

triggers = ["请记住", "记住这个", "记住以下", "记住：", "记住:", "记住", "记下来",
            "remember that", "remember this", "keep in mind that", "keep in mind", "remember"]

is_remember = any(t.lower() in text.lower() for t in triggers)
if not is_remember:
    sys.exit(0)  # exit 0 so set -e does not kill the script

# Exclude first-person recall patterns ("I remember..." / "我记得..." are recall, not commands)
exclude_recall = [
    r"\b(i|we)\s+remember\b",
    r"我记得",
    r"我想起来了",
    r"我回想",
    r"我记起",
]
for p in exclude_recall:
    if re.search(p, text, re.IGNORECASE):
        is_remember = False
        break

if not is_remember:
    sys.exit(0)

content = text
for t in sorted(triggers, key=len, reverse=True):
    content = re.sub(r"(?i)" + re.escape(t) + r"\s*[,:：]?\s*", "", content, count=1)
content = content.strip()
content = re.sub(r"[。\.!！?？]+$", "", content)

if len(content) >= 3:
    print(content)
' 2>/dev/null || true)

if [[ -n "$REMEMBER_CONTENT" ]]; then
    # Detect sensitive content (API keys, passwords, tokens, secrets)
    IS_SENSITIVE=$(echo "$REMEMBER_CONTENT" | python3 -c '
import sys, re
text = sys.stdin.read().lower()
patterns = [
    r"api\s*(?:key|token|secret)",
    r"password", r"passwd", r"pwd",
    r"secret", r"credential",
    r"token\s*[:=]",
    r"private\s*key", r"ssh\s*key",
    r"access\s*(?:key|token|secret)",
    r"auth\s*(?:key|token|secret)",
]
for p in patterns:
    if re.search(p, text):
        sys.exit(0)
sys.exit(1)
' 2>/dev/null && echo "yes" || echo "no")

    if [[ "$IS_SENSITIVE" == "yes" ]]; then
        # Save to encrypted vault
        "$MEME_BIN" add "$REMEMBER_CONTENT" --type feedback --importance 0.5 --sensitive 2>/dev/null || true
    else
        # Normal save
        "$MEME_BIN" add "$REMEMBER_CONTENT" --type feedback --importance 0.5 2>/dev/null || true
    fi
fi
# ------------------------------------------------

# Extract keywords (remove common stop words, take significant words)
KEYWORDS=$(echo "$PROMPT" | python3 -c '
import sys, re
text = sys.stdin.read().lower()
# Stop words
stops = {"the","a","an","is","are","was","were","be","been","being",
         "have","has","had","do","does","did","will","would","could",
         "should","may","might","shall","can","need","must",
         "i","me","my","we","our","you","your","he","she","it",
         "they","them","this","that","these","those",
         "and","or","but","if","then","else","when","at","by","for",
         "with","about","against","between","through","during","before",
         "after","above","below","to","from","up","down","in","out",
         "on","off","over","under","again","further","than","once",
         "here","there","why","how","all","each","every","both","few",
         "more","most","other","some","such","no","nor","not","only",
         "own","same","so","very","just","because","as","until","while",
         "of","into","what","which","who","whom","whose",
         "help","please","want","know","think","make","get","go","come",
         "好的","是的","对的","请","帮","我","你","他","她","它","吗","呢","吧"}
words = re.findall(r"[a-zA-Z0-9_]+|[一-鿿]+", text)
keywords = [w for w in words if w not in stops and len(w) > 1]
# Take top 8 keywords
print(" ".join(keywords[:8]))
' 2>/dev/null)

if [[ -z "$KEYWORDS" ]]; then
    echo '{"continue":true,"suppressOutput":true}'
    exit 0
fi

# Search memories using meme CLI
SEARCH_RESULT=$("$MEME_BIN" search "$KEYWORDS" --format json 2>/dev/null || echo "[]")

# Check if we got results
if [[ "$SEARCH_RESULT" == "[]" || -z "$SEARCH_RESULT" ]]; then
    # Try semantic fallback via daydream clusters
    SEMANTIC_RESULT=$(python3 -c '
import json, os, sys
clusters_file = os.path.expanduser("~/.meme/meta/clusters.json")
if not os.path.exists(clusters_file):
    sys.exit(0)
with open(clusters_file) as f:
    data = json.load(f)
user_kw = set("$KEYWORDS".lower().split())
matches = []
for c in data.get("clusters", []):
    cluster_kw = set(k.lower() for k in c.get("keywords", []))
    overlap = user_kw & cluster_kw
    if overlap:
        matches.append((len(overlap), c["core_id"]))
matches.sort(reverse=True)
for _, core_id in matches[:2]:
    print(core_id)
' 2>/dev/null)

    if [[ -n "$SEMANTIC_RESULT" ]]; then
        # Build pseudo search result from cluster cores
        SEARCH_RESULT=$(echo "$SEMANTIC_RESULT" | python3 -c '
import json, os, sys
from meme.utils import find_memory_by_id, load_memory
results = []
for line in sys.stdin:
    mem_id = line.strip()
    if not mem_id:
        continue
    path = find_memory_by_id(mem_id)
    if not path:
        continue
    try:
        meta, body = load_memory(path)
        results.append({
            "id": mem_id,
            "title": mem_id,
            "importance": meta.get("importance", 0.5),
            "tier": "archive",
            "tags": meta.get("tags", []),
            "content": body[:300],
            "sensitive": meta.get("sensitive", False),
        })
    except Exception:
        continue
print(json.dumps(results))
')
    fi

    if [[ "$SEARCH_RESULT" == "[]" || -z "$SEARCH_RESULT" ]]; then
        # Log query miss for session-end analysis
        python3 -c "
import json, os, time
misses_file = os.path.expanduser('~/.meme/meta/query_misses.jsonl')
os.makedirs(os.path.dirname(misses_file), exist_ok=True)
entry = {
    'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ'),
    'keywords': '$KEYWORDS',
    'session_id': open(os.path.expanduser('~/.meme/meta/session_heat.json')).read().split('\"session_id\": \"')[1].split('\"')[0] if os.path.exists(os.path.expanduser('~/.meme/meta/session_heat.json')) else 'unknown'
}
with open(misses_file, 'a') as f:
    f.write(json.dumps(entry) + '\n')
" 2>/dev/null
        echo '{"continue":true,"suppressOutput":true}'
        exit 0
    fi
fi

# Format results as additionalContext
CONTEXT=$(echo "$SEARCH_RESULT" | python3 -c '
import json, sys
try:
    results = json.load(sys.stdin)
    if not results:
        sys.exit(0)
    lines = ["## Related Memories (from Meme)"]
    for r in results[:5]:  # Max 5 results
        tier = r.get("tier", "unknown")
        title = r.get("title", "Untitled")
        importance = r.get("importance", 0)
        tags = ", ".join(r.get("tags", []))
        is_sensitive = r.get("sensitive") or tier == "vault"
        cold_mark = " [cold] not accessed recently" if tier == "cold" else ""
        vault_mark = " [encrypted]" if is_sensitive else ""
        lines.append("")
        lines.append(f"### {title} (importance: {importance}, tier: {tier}){cold_mark}{vault_mark}")
        if tags:
            lines.append(f"Tags: {tags}")
        if is_sensitive:
            lines.append("[encrypted — authorization required]")
        else:
            lines.append(r.get("content", "")[:300])
    print("\n".join(lines))
except Exception as e:
    pass
' 2>/dev/null)

if [[ -z "$CONTEXT" ]]; then
    echo '{"continue":true,"suppressOutput":true}'
else
    # Update session heat (track memory IDs, not keywords)
    echo "$SEARCH_RESULT" | python3 -c '
import json, os, time, sys
heat_file = os.path.expanduser("~/.meme/meta/session_heat.json")
try:
    with open(heat_file) as f:
        heat = json.load(f)
    results = json.load(sys.stdin)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in results[:5]:
        mem_id = r.get("id")
        if mem_id:
            heat["heat_map"][mem_id] = {
                "accessed_at": now,
                "heat": 1.0
            }
    with open(heat_file, "w") as f:
        json.dump(heat, f, indent=2)
except:
    pass
' 2>/dev/null

    ESCAPED=$(echo "$CONTEXT" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')
    echo "{\"continue\":true,\"hookSpecificOutput\":{\"hookEventName\":\"UserPromptSubmit\",\"additionalContext\":$ESCAPED}}"
fi
