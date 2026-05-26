#!/usr/bin/env bash
# ========================================
# Meme SessionStart Hook
# ========================================
# Loads working memory + correction memories
# Outputs JSON with additionalContext for Claude Code

set -euo pipefail

MEME_HOME="${MEME_HOME:-$HOME/.meme}"
MEME_BIN="$MEME_HOME/bin/meme"

# If meme CLI not installed, silently skip
if [[ ! -x "$MEME_BIN" ]]; then
    echo '{"continue":true,"suppressOutput":true}'
    exit 0
fi

# Initialize session heat file
SESSION_ID="$(date +%Y%m%d)-$(head -c 4 /dev/urandom | xxd -p)"
SESSION_STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$MEME_HOME/meta/session_heat.json" <<HEAT
{
  "session_id": "$SESSION_ID",
  "started": "$SESSION_STARTED",
  "heat_map": {}
}
HEAT

# Background version check (non-blocking, result read after building context)
VERSION_CHECK_FILE="$MEME_HOME/meta/version_check.json"
rm -f "$VERSION_CHECK_FILE"
"$MEME_BIN" upgrade --check > /dev/null 2>&1 &
VERSION_PID=$!

# Collect working memories
WORKING_CONTEXT=""
TOKEN_BUDGET=2000
TOKEN_COUNT=0

# hard_rules.md always loads first (if exists)
HARD_RULES="$MEME_HOME/working/hard_rules.md"
if [[ -f "$HARD_RULES" ]]; then
    CONTENT=$(cat "$HARD_RULES")
    CHARS=${#CONTENT}
    TOKENS=$(( CHARS / 4 ))
    WORKING_CONTEXT="$CONTENT"
    TOKEN_COUNT=$(( TOKEN_COUNT + TOKENS ))
fi

# Load other working memories sorted by importance
for f in "$MEME_HOME"/working/*.md; do
    [[ -f "$f" ]] || continue
    BASENAME=$(basename "$f")
    [[ "$BASENAME" == "hard_rules.md" ]] && continue

    # Extract importance from frontmatter
    IMPORTANCE=$(grep -m1 '^importance:' "$f" 2>/dev/null | sed 's/importance: *//' | tr -d ' ')
    [[ -z "$IMPORTANCE" ]] && IMPORTANCE="0.5"

    CONTENT=$(cat "$f")
    CHARS=${#CONTENT}
    TOKENS=$(( CHARS / 4 ))

    if (( TOKEN_COUNT + TOKENS <= TOKEN_BUDGET )); then
        WORKING_CONTEXT="$WORKING_CONTEXT

---
$CONTENT"
        TOKEN_COUNT=$(( TOKEN_COUNT + TOKENS ))
    fi
done

# Load correction memories related to current directory
CORRECTION_CONTEXT=""
PROJECT_NAME=$(basename "$(pwd)")
for f in "$MEME_HOME"/archive/corrections/*.md; do
    [[ -f "$f" ]] || continue
    # Check if correction is relevant to current project or general
    if grep -ql "$PROJECT_NAME\|general" "$f" 2>/dev/null; then
        CONTENT=$(head -20 "$f")
        CORRECTION_CONTEXT="$CORRECTION_CONTEXT
$CONTENT"
    fi
done

# Build final context
FINAL_CONTEXT=""
if [[ -n "$WORKING_CONTEXT" ]]; then
    FINAL_CONTEXT="## Working Memory

$WORKING_CONTEXT"
fi

if [[ -n "$CORRECTION_CONTEXT" ]]; then
    FINAL_CONTEXT="$FINAL_CONTEXT

## Error Correction Patterns
$CORRECTION_CONTEXT"
fi

# Wait for version check to finish (max 3s)
wait "$VERSION_PID" 2>/dev/null || true
if [[ -f "$VERSION_CHECK_FILE" ]]; then
    LATEST=$(python3 -c "import json; d=json.load(open('$VERSION_CHECK_FILE')); print(d.get('latest',''))" 2>/dev/null || true)
    CURRENT_VER=$(python3 -c "import json; d=json.load(open('$VERSION_CHECK_FILE')); print(d.get('current',''))" 2>/dev/null || true)
    if [[ -n "$LATEST" && -n "$CURRENT_VER" ]]; then
        FINAL_CONTEXT="$FINAL_CONTEXT

## Update Available
Meme v$LATEST is available (current: v$CURRENT_VER).
To upgrade: \`uvx memectl@$LATEST install\` or \`pipx run memectl install\`"
    fi
fi

if [[ -z "$FINAL_CONTEXT" ]]; then
    echo '{"continue":true,"suppressOutput":true}'
else
    # Escape for JSON
    ESCAPED=$(echo "$FINAL_CONTEXT" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')
    echo "{\"continue\":true,\"hookSpecificOutput\":{\"hookEventName\":\"SessionStart\",\"additionalContext\":$ESCAPED}}"
fi
