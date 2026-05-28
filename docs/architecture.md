# Meme Architecture

## Overview

Meme is a centralized, tiered memory system with a knowledge graph. It provides persistent, retrievable memory for AI assistants through a three-tier model and graph-based retrieval.

## Three-Tier Memory Model

```
┌─────────────────────────────────────────────────┐
│  Working (importance >= 0.8)                     │
│  Auto-loaded per session, token budget 2000      │
├─────────────────────────────────────────────────┤
│  Archive (0.2 <= importance < 0.8)               │
│  Loaded via graph traversal with distance decay  │
│  load_weight = importance x (0.4 ^ distance)     │
├─────────────────────────────────────────────────┤
│  Cold (importance < 0.2)                         │
│  BM25 searchable; auto-warms after 3 hits        │
└─────────────────────────────────────────────────┘
```

- **Working**: High-importance memories always available in context
- **Archive**: Retrieved through keyword search and graph traversal
- **Cold**: Searchable but not loaded by default; revivable on repeated access

## Knowledge Graph

Memories link to each other via `[[link]]` syntax in frontmatter:

```yaml
---
id: mem_20260528_xxx
type: feedback
links: [mem_20260527_yyy, mem_20260526_zzz]
---
```

Retrieval follows BFS expansion:
- Distance 0: Full content of hit memory
- Distance 1: Summary of linked memories
- Distance 2+: Title only

## Claude Code Integration

Three hooks auto-inject memory context:

| Hook | Trigger | Behavior |
|------|---------|----------|
| SessionStart | Session start | Load working memories + corrections |
| UserPromptSubmit | User input | Keyword search -> graph traversal -> inject context |
| SessionEnd | Session end | Persist access counts, auto promote/demote |

## Module Structure

```
src/meme/
├── constants.py      # Directory layout, thresholds
├── config.py         # TOML configuration management
├── utils.py          # Frontmatter, file discovery, git, index/graph
├── vault.py          # Touch ID + keyring + Fernet encryption
├── log.py            # Logging setup (file + console)
├── cli.py            # Argument parser and main entry
├── core.py           # Compatibility re-export layer
└── commands/         # CLI command implementations
    ├── setup.py
    ├── memory.py
    ├── ingest.py
    ├── lifecycle.py
    ├── links.py      # daydream, dream, config
    ├── maintenance.py
    └── system.py
```

## Configuration

User config lives in `~/.meme/config.toml`:

```toml
[dream]
enabled = true
schedule = "0 3 * * *"
threshold = 0.4
auto_apply = true
```

Loaded with defaults merged: `load_config()` returns `DEFAULT_CONFIG` overridden by user values.

## Memory Consolidation (Daydream)

Two modes:
- **Daydream** (`meme daydream`): Manual semantic clustering + link suggestion
- **Dream** (`meme dream`): Nightly automated consolidation

Both use Jaccard similarity on significant words + tag overlap + type matching to cluster memories. Clusters reveal duplicates and hidden relationships.
