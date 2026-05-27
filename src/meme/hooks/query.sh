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
    echo '{"continue":true,"suppressOutput":true}'
    exit 0
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
        content = r.get("content", "")[:300]
        cold_mark = " [cold] not accessed recently" if tier == "cold" else ""
        lines.append("")
        lines.append(f"### {title} (importance: {importance}, tier: {tier}){cold_mark}")
        if tags:
            lines.append(f"Tags: {tags}")
        lines.append(content)
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
