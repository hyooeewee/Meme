# Meme — Centralized Memory System

A personal memory management system built on knowledge graphs, providing persistent, tiered, and retrievable memory for AI assistants.

## Features

- **Three-tier memory model**: Working (loaded every session) → Archive (graph-traversal retrieval) → Cold (BM25 searchable, warmable)
- **Knowledge graph**: Memories reference each other via `[[link]]` syntax; BFS traversal retrieval
- **Importance decay**: Long-unaccessed memories auto-demote; frequently used ones auto-promote
- **Error correction**: Remember operational fixes (CLI command changes, etc.) to avoid repeated mistakes
- **Learning & ingestion**: Learn from URLs, documents, and conversations
- **Encrypted vault**: Sensitive memories stored with AES-256 encryption via macOS Keychain
- **Obsidian compatible**: Standard `.md` files work in Obsidian with graph view and backlinks
- **Claude Code integration**: Auto-injects memory context via hooks

## Requirements

- Python 3.10+
- macOS / Linux
- `uv` (recommended) or `pip`

## Installation

### Method 1: One-line install

```bash
curl -sSL https://raw.githubusercontent.com/hyooeewee/Meme/main/install.sh | bash
```

### Method 2: uv (recommended)

```bash
uv tool install memectl
meme setup
```

### Method 3: pip

```bash
pip install memectl
meme setup
```

### Method 4: From source (development)

```bash
git clone https://github.com/hyooeewee/Meme.git
cd Meme
uv pip install -e .
meme setup --dev
```

The `--dev` flag symlinks hooks and CLI scripts instead of copying, so source changes take effect immediately.

### Setup options

```bash
# Basic setup
meme setup

# Setup + migrate existing Claude Code memories
meme setup --migrate
```

### Verify

```bash
meme version
meme doctor
```

## Quick Start

```bash
# Add a memory
meme add "Use uv for Python dependencies, not pip" --type feedback --importance 0.8

# Search memories
meme search "python dependencies"

# List all memories
meme list

# List by tier
meme list --tier working

# Graph traversal (expand from a memory via links)
meme query mem_xxx

# Learn from URL
meme learn https://docs.example.com/guide

# Learn from file
meme learn --file ./notes.md
```

## Command Reference

### Memory management

| Command | Description |
|---------|-------------|
| `meme add "content" [options]` | Add a new memory |
| `meme list [options]` | List memories |
| `meme search "keyword"` | BM25 keyword search |
| `meme query mem_id` | Graph traversal retrieval |
| `meme edit mem_id` | Edit memory content and metadata |
| `meme delete mem_id` | Delete a memory |
| `meme forget mem_id [options]` | Forget a memory (soft / hard / purge) |

**add options:**
- `--type TYPE` — feedback / project / user / reference / knowledge / correction
- `--importance N` — 0.0~1.0 (default 0.5)
- `--tags TAG1,TAG2` — Tags
- `--links mem_a,mem_b` — Link to other memories
- `--sensitive` — Store in encrypted vault

**list options:**
- `--tier TIER` — Filter by tier: working / archive / cold
- `--tag TAG` — Filter by tag
- `--sort ORDER` — Sort by: importance / recent / heat
- `--format FORMAT` — Output format: text / json

**forget options:**
- `--hard` — Delete from filesystem
- `--hard --purge` — Delete + scrub from git history

### Learning & ingestion

| Command | Description |
|---------|-------------|
| `meme learn <url>` | Fetch and distill content from URL |
| `meme learn --file <path>` | Extract and distill from local file |

### Lifecycle

| Command | Description |
|---------|-------------|
| `meme decay [--dry-run]` | Run importance decay scan |
| `meme promote mem_id` | Manually promote to working tier |
| `meme demote mem_id` | Manually demote |
| `meme warm mem_id` | Warm a cold memory back to archive |
| `meme link mem_a mem_b` | Create a bidirectional link |
| `meme suggest-links` | Suggest new links based on usage patterns |
| `meme heat` | Show current session heat |

### Migration

| Command | Description |
|---------|-------------|
| `meme import --from claude` | Migrate from Claude Code project memories |
| `meme import --from claude-global` | Migrate from Claude Code global config |
| `meme import --from codex [--path]` | Migrate from Codex |
| `meme sync --incremental` | Incremental sync |

### Maintenance

| Command | Description |
|---------|-------------|
| `meme doctor [--fix]` | Health check + auto-fix |
| `meme backup` | Manual backup |
| `meme gc` | Clean old backups |
| `meme reindex` | Rebuild index.json + graph.json |
| `meme stats` | Statistics |
| `meme export [--format json\|md]` | Export all memories |

### System

| Command | Description |
|---------|-------------|
| `meme version` | Show version + check for updates |
| `meme upgrade [--check] [--force]` | Self-upgrade |
| `meme changelog` | View version history |
| `meme uninstall [--keep-data]` | Uninstall Meme |

## Memory Types

| Type | Purpose | Default importance |
|------|---------|-------------------|
| `feedback` | Corrections and preferences | 0.6 |
| `project` | Project context and status | 0.5 |
| `user` | User identity, background, preferences | 0.8 |
| `reference` | External resource pointers | 0.4 |
| `knowledge` | Learned from docs/URLs | 0.5 |
| `correction` | CLI changes and operational fixes | 0.9 |

## Three-tier Memory Model

```
┌─────────────────────────────────────────────────┐
│  Working (importance >= 0.8)                     │
│  Auto-loaded per session, token budget 2000      │
├─────────────────────────────────────────────────┤
│  Archive (0.2 <= importance < 0.8)               │
│  Loaded via graph traversal with distance decay  │
│  load_weight = importance × (0.4 ^ distance)     │
├─────────────────────────────────────────────────┤
│  Cold (importance < 0.2)                         │
│  BM25 searchable; auto-warms after 3 hits        │
└─────────────────────────────────────────────────┘
```

## Graph Traversal Retrieval

When a query hits a memory node, BFS expands along the knowledge graph:

```
Query: "docker permission issue"
  │
  ├─ Hit: feedback_docker_permission.md (distance: 0) → full load
  │
  ├─ 1-degree links (distance: 1) → summary load
  │   ├─ install_permission.md
  │   └─ project_ecoctrl.md
  │
  └─ 2-degree links (distance: 2) → title only
      └─ knowledge_docker.md
```

## Obsidian Compatibility

Memories are standard `.md` files with YAML frontmatter. Obsidian's `[[wiki-link]]` syntax is natively supported. Open `~/.meme/` as an Obsidian vault to browse the graph view and backlinks.

## Claude Code Integration

`meme setup` auto-registers three hooks in `~/.claude/settings.json`:

| Hook | Trigger | Behavior |
|------|---------|----------|
| SessionStart | Session start | Load working memories + corrections |
| UserPromptSubmit | User input | Keyword search → graph traversal → inject context |
| SessionEnd | Session end | Persist access counts, auto promote/demote |

## Encrypted Vault

Sensitive memories (API keys, passwords) are encrypted with AES-256 via macOS Keychain:

```bash
# Add encrypted memory
meme add "API key: sk-xxx" --sensitive --type knowledge

# Search shows summary only; decryption requires Keychain authorization
meme search "api key"
```

## Development

```bash
# Install in dev mode (symlinks instead of copies)
uv pip install -e .
meme setup --dev

# Run tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ -v --cov=src/meme --cov-report=term-missing
```

## Directory Structure

```
~/.meme/
├── MEMORY.md                    # Main index
├── working/                     # Tier 1: always loaded
├── archive/                     # Tier 2: graph-traversal loaded
│   ├── projects/
│   ├── feedback/
│   └── knowledge/
├── cold/                        # Tier 3: search-only
├── vault/                       # Encrypted memories
├── backups/                     # tar.gz backups
├── meta/
│   ├── index.json               # Full index
│   ├── graph.json               # Adjacency list
│   └── session_heat.json        # Session heat (temporary)
└── bin/
    ├── meme                     # CLI entry
    ├── query.sh                 # UserPromptSubmit hook
    ├── session_start.sh         # SessionStart hook
    └── session_end.sh           # SessionEnd hook
```

## Uninstall

```bash
# Uninstall but keep data
meme uninstall --keep-data

# Full uninstall
meme uninstall
```

## License

MIT
