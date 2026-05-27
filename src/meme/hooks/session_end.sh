#!/usr/bin/env bash
# ========================================
# Meme SessionEnd Hook
# ========================================
# Writes back heat/access_count, promotes memories,
# cleans up session state

set -euo pipefail

MEME_HOME="${MEME_HOME:-$HOME/.meme}"
MEME_BIN="$MEME_HOME/bin/meme"

# If meme CLI not installed, silently skip
if [[ ! -x "$MEME_BIN" ]]; then
    exit 0
fi

HEAT_FILE="$MEME_HOME/meta/session_heat.json"

# If no heat file, nothing to do
if [[ ! -f "$HEAT_FILE" ]]; then
    exit 0
fi

# Process heat map and update access counts
python3 << 'PYEOF'
import json, os, glob, re
from datetime import datetime

MEME_HOME = os.path.expanduser("~/.meme")
heat_file = os.path.join(MEME_HOME, "meta", "session_heat.json")

try:
    with open(heat_file) as f:
        session = json.load(f)
except:
    exit(0)

heat_map = session.get("heat_map", {})
if not heat_map:
    # Clean up and exit
    os.remove(heat_file)
    exit(0)

# Update access counts for accessed memories
for mem_id, info in heat_map.items():
    heat = info.get("heat", 0)
    if heat <= 0:
        continue

    # Find the memory file
    for tier_dir in ["working", "archive"]:
        for root, dirs, files in os.walk(os.path.join(MEME_HOME, tier_dir)):
            for f in files:
                if not f.endswith(".md"):
                    continue
                filepath = os.path.join(root, f)
                try:
                    with open(filepath) as fh:
                        content = fh.read()
                    # Check if this file matches the memory id
                    match = re.search(r'^id:\s*(.+)$', content, re.MULTILINE)
                    if match and match.group(1).strip() == mem_id:
                        # Update access_count and last_accessed
                        now = datetime.utcnow().strftime("%Y-%m-%d")
                        content = re.sub(
                            r'^last_accessed:.*$',
                            f'last_accessed: {now}',
                            content,
                            flags=re.MULTILINE
                        )
                        count_match = re.search(r'^access_count:\s*(\d+)', content, re.MULTILINE)
                        if count_match:
                            new_count = int(count_match.group(1)) + 1
                            content = re.sub(
                                r'^access_count:.*$',
                                f'access_count: {new_count}',
                                content,
                                flags=re.MULTILINE
                            )
                            # Auto-promote to working if threshold met
                            if new_count >= 5 and tier_dir == "archive":
                                imp_match = re.search(r'^importance:\s*([\d.]+)', content, re.MULTILINE)
                                if imp_match and float(imp_match.group(1)) >= 0.7:
                                    # Move to working
                                    dest = os.path.join(MEME_HOME, "working", f)
                                    if not os.path.exists(dest):
                                        os.rename(filepath, dest)
                                        filepath = dest
                        with open(filepath, "w") as fh:
                            fh.write(content)
                        break
                except:
                    continue

# Clean up session heat file
try:
    os.remove(heat_file)
except:
    pass

PYEOF

echo '{"continue":true}'
