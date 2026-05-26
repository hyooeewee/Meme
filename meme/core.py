# meme/core.py — Meme CLI core logic

"""
Meme — A centralized, tiered memory system with knowledge graph.

Three-tier model:
  Working  (importance >= 0.8) — always loaded
  Archive  (0.2 ~ 0.8)        — graph traversal retrieval
  Cold     (< 0.2)             — BM25 search only, revivable
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
from collections import deque
from pathlib import Path

import yaml

# ========================================
# Package data helpers
# ========================================

def _get_package_resource_path(relative_path: str):
    """Get a path to a package resource, works in both package and script mode.

    Returns a pathlib.Path if the resource exists as a file, or None.
    Tries importlib.resources first (package mode), then falls back to
    relative path from this file (script mode).
    """
    try:
        from importlib.resources import files
        ref = files("meme").joinpath(relative_path)
        # For real files on disk, .locate() gives us a path
        p = Path(str(ref))
        if p.exists():
            return p
    except (ModuleNotFoundError, TypeError, FileNotFoundError):
        pass
    # Fallback: relative to this file (script mode, e.g. `uv run meme-cli`)
    fallback = Path(__file__).resolve().parent / relative_path
    if fallback.exists():
        return fallback
    # Also check parent directory (when running from repo root)
    fallback2 = Path(__file__).resolve().parent.parent / "hooks" / Path(relative_path).name
    if relative_path.startswith("hooks/") and fallback2.exists():
        return fallback2
    return None

# ========================================
# Constants
# ========================================

MEME_HOME = Path.home() / ".meme"
WORKING_DIR = MEME_HOME / "working"
ARCHIVE_DIR = MEME_HOME / "archive"
COLD_DIR = MEME_HOME / "cold"
VAULT_DIR = MEME_HOME / "vault"
BACKUPS_DIR = MEME_HOME / "backups"
META_DIR = MEME_HOME / "meta"
BIN_DIR = MEME_HOME / "bin"

INDEX_PATH = META_DIR / "index.json"
GRAPH_PATH = META_DIR / "graph.json"
VERSION_PATH = META_DIR / "version.json"
IMPORT_STATE_PATH = META_DIR / "import_state.json"
SESSION_HEAT_PATH = META_DIR / "session_heat.json"
CONFLICT_LOG_PATH = META_DIR / "conflict_log.jsonl"
DECAY_LOG_PATH = META_DIR / "decay_log.jsonl"
FORGOTTEN_INDEX_PATH = META_DIR / "forgotten_index.json"
VERSION_CHECK_PATH = META_DIR / "version_check.json"

MEMORY_MD_PATH = MEME_HOME / "MEMORY.md"

CURRENT_VERSION = "0.1.0"
CURRENT_SCHEMA = 1

TOKEN_BUDGET_WORKING = 2000  # tokens
TOKEN_BUDGET_HOOK = 8000     # chars for additionalContext

TIER_WORKING_THRESHOLD = 0.8
TIER_ARCHIVE_THRESHOLD = 0.2

SUBDIRS = {
    "feedback": ARCHIVE_DIR / "feedback",
    "project": ARCHIVE_DIR / "projects",
    "knowledge": ARCHIVE_DIR / "knowledge",
    "correction": ARCHIVE_DIR / "corrections",
    "user": WORKING_DIR,
    "reference": ARCHIVE_DIR / "feedback",
}

FRONTMATTER_KEYS = [
    "id", "type", "importance", "created", "last_accessed",
    "access_count", "tags", "links", "superseded_by", "supersedes",
    "forgotten", "forgotten_at", "forgotten_reason",
    "sensitive", "source_url", "source_file",
    "corrects", "scope", "wrong_pattern", "correct_pattern",
]

# ========================================
# Utility: Frontmatter
# ========================================

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown text. Returns (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    return meta, parts[2].lstrip("\n")


def render_frontmatter(meta: dict, body: str) -> str:
    """Render memory file with YAML frontmatter."""
    # Filter to known keys + preserve unknown ones
    lines = ["---"]
    for key in FRONTMATTER_KEYS:
        if key in meta:
            val = meta[key]
            if val is None:
                lines.append(f"{key}: null")
            elif isinstance(val, bool):
                lines.append(f"{key}: {'true' if val else 'false'}")
            elif isinstance(val, list):
                if not val:
                    lines.append(f"{key}: []")
                else:
                    lines.append(f"{key}: {yaml.dump(val, default_flow_style=True).strip()}")
            elif isinstance(val, dict):
                lines.append(f"{key}:")
                for k, v in val.items():
                    lines.append(f"  {k}: {v}")
            else:
                lines.append(f"{key}: {val}")
    # Preserve any unknown keys
    known = set(FRONTMATTER_KEYS)
    for key, val in meta.items():
        if key not in known:
            lines.append(f"{key}: {yaml.dump(val, default_flow_style=True).strip()}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


def load_memory(path: Path) -> tuple[dict, str]:
    """Load a memory file, return (meta, body). Handles vault .enc files."""
    if path.suffix == ".enc":
        result = load_vault_memory(path.stem)
        if result:
            return result
        return {"id": path.stem, "sensitive": True}, "[encrypted — unlock with OS keyring]"
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text)


def save_memory(path: Path, meta: dict, body: str):
    """Save a memory file with frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_frontmatter(meta, body), encoding="utf-8")

# ========================================
# Utility: Token counting
# ========================================

def count_tokens(text: str) -> int:
    """Rough token count: chars / 4."""
    return len(text) // 4 + 1

# ========================================
# Utility: ID generation
# ========================================

def generate_id(mem_type: str, slug: str = "") -> str:
    """Generate a memory ID like mem_20260526_slug."""
    date = datetime.date.today().strftime("%Y%m%d")
    if not slug:
        slug = hashlib.md5(f"{date}{mem_type}{os.urandom(8).hex()}".encode()).hexdigest()[:8]
    slug = re.sub(r"[^a-z0-9_]", "_", slug.lower())
    return f"mem_{date}_{slug}"

# ========================================
# Utility: Git
# ========================================

def git_run(*args, cwd: Path = MEME_HOME, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in the meme repo."""
    return subprocess.run(
        ["git"] + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def git_commit(message: str, files: list[Path] | None = None):
    """Auto-commit changes to the meme repo."""
    if not (MEME_HOME / ".git").exists():
        return
    try:
        if files:
            for f in files:
                git_run("add", str(f))
        else:
            git_run("add", "-A")
        # Check if there's anything to commit
        result = git_run("status", "--porcelain", check=False)
        if result.stdout.strip():
            git_run("commit", "-m", message)
    except subprocess.CalledProcessError:
        pass  # Silently skip git errors (e.g., empty commit)

# ========================================
# Utility: Index & Graph
# ========================================

def load_index() -> dict:
    if INDEX_PATH.exists():
        return json.loads(INDEX_PATH.read_text())
    return {}


def save_index(index: dict):
    INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False))


def load_graph() -> dict:
    if GRAPH_PATH.exists():
        return json.loads(GRAPH_PATH.read_text())
    return {}


def save_graph(graph: dict):
    GRAPH_PATH.write_text(json.dumps(graph, indent=2, ensure_ascii=False))


def load_forgotten_index() -> dict:
    if FORGOTTEN_INDEX_PATH.exists():
        return json.loads(FORGOTTEN_INDEX_PATH.read_text())
    return {}


def save_forgotten_index(idx: dict):
    FORGOTTEN_INDEX_PATH.write_text(json.dumps(idx, indent=2, ensure_ascii=False))

# ========================================
# Utility: Symlink
# ========================================

def ensure_symlink(link_path: Path, target: Path):
    """Create or update a symlink."""
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink():
        if link_path.resolve() == target.resolve():
            return
        link_path.unlink()
    elif link_path.exists():
        return  # Don't overwrite real files
    link_path.symlink_to(target)

# ========================================
# Utility: File discovery
# ========================================

def find_all_memories(include_cold: bool = False, include_forgotten: bool = False) -> list[Path]:
    """Find all memory .md and vault .enc files."""
    paths = []
    for d in [WORKING_DIR, ARCHIVE_DIR]:
        if d.exists():
            paths.extend(d.rglob("*.md"))
    if include_cold and COLD_DIR.exists():
        paths.extend(COLD_DIR.rglob("*.md"))
    # Include vault encrypted memories
    if VAULT_DIR.exists():
        paths.extend(VAULT_DIR.glob("*.enc"))
    if not include_forgotten:
        forgotten = load_forgotten_index()
        forgotten_ids = set(forgotten.keys())
        paths = [p for p in paths if not _is_forgotten(p, forgotten_ids)]
    return paths


def _is_forgotten(path: Path, forgotten_ids: set) -> bool:
    try:
        meta, _ = load_memory(path)
        return meta.get("id") in forgotten_ids or meta.get("forgotten")
    except Exception:
        return False


def find_memory_by_id(mem_id: str) -> Path | None:
    """Find a memory file by its ID."""
    for p in find_all_memories(include_cold=True):
        try:
            meta, _ = load_memory(p)
            if meta.get("id") == mem_id:
                return p
        except Exception:
            continue
    return None


# ========================================
# Utility: Vault encryption
# ========================================

VAULT_KEYRING_SERVICE = "meme-memory-system"
VAULT_KEYRING_USER = "vault-key"


def _get_vault_key() -> bytes:
    """Get or create the vault encryption key via OS keyring."""
    import keyring
    key_str = keyring.get_password(VAULT_KEYRING_SERVICE, VAULT_KEYRING_USER)
    if key_str:
        return key_str.encode("utf-8")
    # Generate new key (Fernet key is already URL-safe base64 encoded)
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    keyring.set_password(VAULT_KEYRING_SERVICE, VAULT_KEYRING_USER, key.decode())
    return key


def vault_encrypt(plaintext: str) -> bytes:
    """Encrypt a string for vault storage."""
    from cryptography.fernet import Fernet
    key = _get_vault_key()
    f = Fernet(key)
    return f.encrypt(plaintext.encode("utf-8"))


def vault_decrypt(ciphertext: bytes) -> str:
    """Decrypt vault ciphertext."""
    from cryptography.fernet import Fernet
    key = _get_vault_key()
    f = Fernet(key)
    return f.decrypt(ciphertext).decode("utf-8")


def save_vault_memory(mem_id: str, meta: dict, body: str):
    """Save an encrypted memory to the vault."""
    import base64
    full_content = save_memory_to_string(meta, body)
    encrypted = vault_encrypt(full_content)
    enc_path = VAULT_DIR / f"{mem_id}.enc"
    enc_path.write_bytes(encrypted)
    # Write plaintext index entry (no secrets, just metadata for search)
    index_entry = {
        "id": mem_id,
        "type": meta.get("type", "knowledge"),
        "tags": meta.get("tags", []),
        "summary": body[:200],
        "encrypted": True,
    }
    vault_index_path = VAULT_DIR / "_vault.json"
    vault_index = {}
    if vault_index_path.exists():
        vault_index = json.loads(vault_index_path.read_text())
    vault_index[mem_id] = index_entry
    vault_index_path.write_text(json.dumps(vault_index, indent=2, ensure_ascii=False))
    return enc_path


def load_vault_memory(mem_id: str) -> tuple[dict, str] | None:
    """Load and decrypt a vault memory."""
    enc_path = VAULT_DIR / f"{mem_id}.enc"
    if not enc_path.exists():
        return None
    encrypted = enc_path.read_bytes()
    plaintext = vault_decrypt(encrypted)
    return parse_memory_string(plaintext)


def save_memory_to_string(meta: dict, body: str) -> str:
    """Serialize memory to a single markdown string."""
    yaml_str = yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{yaml_str}---\n\n{body}"


def parse_memory_string(text: str) -> tuple[dict, str]:
    """Parse a memory string into frontmatter and body."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
            return meta, body
    return {}, text


# ========================================
# Utility: Tier classification
# ========================================

def get_tier(meta: dict) -> str:
    """Get the tier of a memory based on importance."""
    imp = meta.get("importance", 0.5)
    if imp >= TIER_WORKING_THRESHOLD:
        return "working"
    elif imp >= TIER_ARCHIVE_THRESHOLD:
        return "archive"
    return "cold"


def get_memory_dir(mem_type: str, tier: str = "archive") -> Path:
    """Get the appropriate directory for a memory."""
    if tier == "working":
        return WORKING_DIR
    elif tier == "cold":
        return COLD_DIR
    return SUBDIRS.get(mem_type, ARCHIVE_DIR / "feedback")

# ========================================
# Utility: MEMORY.md rebuild
# ========================================

def rebuild_memory_md():
    """Rebuild the MEMORY.md index file."""
    working = []
    archive = {"projects": [], "feedback": [], "knowledge": [], "corrections": []}

    for p in WORKING_DIR.glob("*.md"):
        try:
            meta, _ = load_memory(p)
            if not meta.get("forgotten"):
                name = p.stem
                desc = meta.get("tags", [])
                working.append((name, desc, meta.get("importance", 0.5)))
        except Exception:
            continue

    for subdir_name, subdir_path in [
        ("projects", ARCHIVE_DIR / "projects"),
        ("feedback", ARCHIVE_DIR / "feedback"),
        ("knowledge", ARCHIVE_DIR / "knowledge"),
        ("corrections", ARCHIVE_DIR / "corrections"),
    ]:
        if subdir_path.exists():
            for p in subdir_path.glob("*.md"):
                try:
                    meta, _ = load_memory(p)
                    if not meta.get("forgotten"):
                        archive[subdir_name].append((p.stem, meta.get("tags", [])))
                except Exception:
                    continue

    lines = ["# Meme — Memory Index", ""]

    lines.append("## Working Memory (always loaded)")
    if working:
        working.sort(key=lambda x: -x[2])
        for name, tags, imp in working:
            tag_str = f" ({', '.join(tags[:3])})" if tags else ""
            lines.append(f"- [[{name}]]{tag_str}")
    lines.append("")

    lines.append("## Archive Index (graph traversal retrieval)")
    for category, items in archive.items():
        if items:
            lines.append(f"- {category.title()}: " + " | ".join(f"[[{name}]]" for name, _ in items))
    lines.append("")

    lines.extend([
        "## Query Guide",
        "",
        "When you need to recall something not in Working Memory:",
        '1. Search archive by keyword: `meme search "keyword"`',
        "2. Load the hit file, then follow its `[[links]]` for context",
        "3. 1st-degree links: full load. 2nd-degree: title only. 3rd+: ignore.",
    ])

    MEMORY_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

# ========================================
# Command: install
# ========================================

def cmd_install(args):
    """Install the Meme system."""
    # Check if already installed
    if MEME_HOME.exists() and (MEME_HOME / "meta" / "version.json").exists():
        print(f"Meme is already installed at {MEME_HOME}")
        print("Use 'meme upgrade' to update, or 'meme uninstall' first.")
        return

    print("Installing Meme memory system...")

    # Create directories
    for d in [WORKING_DIR, ARCHIVE_DIR, COLD_DIR, VAULT_DIR, BACKUPS_DIR, META_DIR, BIN_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    for sub in ["projects", "feedback", "knowledge", "corrections"]:
        (ARCHIVE_DIR / sub).mkdir(parents=True, exist_ok=True)

    # Init git
    if not (MEME_HOME / ".git").exists():
        git_run("init")
        git_run("checkout", "-b", "main")

    # Write .gitignore
    gitignore = MEME_HOME / ".gitignore"
    gitignore.write_text(
        "vault/*.enc\nbackups/*.tar.gz\nmeta/session_heat.json\ncold/_index.json\n"
        ".upgrade-tmp/\n__pycache__/\n*.pyc\n.DS_Store\n",
        encoding="utf-8",
    )

    # Write initial meta files
    save_index({})
    save_graph({})

    version_data = {
        "installed_version": CURRENT_VERSION,
        "installed_at": datetime.datetime.now().isoformat(),
        "schema_version": CURRENT_SCHEMA,
        "last_upgrade": None,
        "last_doctor": None,
    }
    VERSION_PATH.write_text(json.dumps(version_data, indent=2))

    IMPORT_STATE_PATH.write_text("{}")
    CONFLICT_LOG_PATH.write_text("")
    DECAY_LOG_PATH.write_text("")

    # Write MEMORY.md
    rebuild_memory_md()

    # Install CLI to bin/ (skip if symlink or installed package)
    cli_dst = BIN_DIR / "meme"
    is_symlink = cli_dst.is_symlink()
    cli_src = Path(__file__).resolve()
    if cli_src.exists() and not is_symlink:
        shutil.copy2(cli_src, cli_dst)
        cli_dst.chmod(0o755)

    # Install hook scripts (package-aware, skip if symlinks)
    for hook_file in ["session_start.sh", "query.sh", "session_end.sh"]:
        dst = BIN_DIR / f"meme-{hook_file.replace('_', '-')}"
        if dst.is_symlink():
            continue
        src = _get_package_resource_path(f"hooks/{hook_file}")
        if src:
            shutil.copy2(src, dst)
            dst.chmod(0o755)

    # Create symlinks for Claude Code projects
    _setup_project_symlinks()

    # Register hooks in Claude Code settings
    _register_hooks()

    # Optional: migrate
    if getattr(args, "migrate", False):
        _do_import_claude()
        _do_import_claude_global()

    # Optional: Obsidian
    obsidian_path = getattr(args, "obsidian", None)
    if obsidian_path:
        obsidian_target = Path(obsidian_path).expanduser()
        if obsidian_target.exists():
            meme_link = obsidian_target / "Meme"
            ensure_symlink(meme_link, MEME_HOME)
            print(f"  Obsidian symlink: {meme_link} -> {MEME_HOME}")

    # Initial commit
    git_commit("init: meme memory system installed")

    # Add to PATH if needed
    bin_str = str(BIN_DIR)
    path = os.environ.get("PATH", "")
    if bin_str not in path:
        _setup_path(bin_str)

    print(f"\nMeme installed successfully at {MEME_HOME}")
    print("  Run 'meme --help' to get started.")


def _setup_path(bin_str: str):
    """Auto-detect shell and add bin dir to PATH."""
    import platform
    if platform.system() == "Windows":
        print(f"\n  [!] Windows detected. Please add to PATH manually:")
        print(f"      setx PATH \"%PATH%;{bin_str}\"")
        print(f"  Or run inside WSL for full support.")
        return

    shell = os.environ.get("SHELL", "")
    export_line = f'export PATH="{bin_str}:$PATH"'
    marker = "# meme-memory-system"

    # Determine rc file
    rc_file = None
    if "zsh" in shell:
        rc_file = Path.home() / ".zshrc"
    elif "bash" in shell:
        # macOS ships zsh by default; bash users go to .bash_profile
        rc_file = Path.home() / ".bash_profile"
    elif "fish" in shell:
        # Fish uses a different syntax
        export_line = f'set -gx PATH "{bin_str}" $PATH'
        rc_file = Path.home() / ".config" / "fish" / "config.fish"
    else:
        rc_file = Path.home() / ".profile"

    # Check if already configured
    if rc_file.exists():
        content = rc_file.read_text(encoding="utf-8")
        if bin_str in content:
            print(f"  PATH already configured in {rc_file.name}")
            return

    # Append to rc file
    try:
        rc_file.parent.mkdir(parents=True, exist_ok=True)
        with open(rc_file, "a", encoding="utf-8") as f:
            f.write(f"\n{marker}\n{export_line}\n")
        print(f"  Added to PATH in {rc_file}")
        print(f"  Run 'source {rc_file}' or restart your shell.")
    except Exception as e:
        print(f"\n  [!] Could not write to {rc_file}: {e}")
        print(f"  Please add manually: {export_line}")


def _setup_project_symlinks():
    """Set up symlinks in Claude Code project memory directories."""
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return
    for proj_dir in claude_projects.iterdir():
        if not proj_dir.is_dir():
            continue
        memory_dir = proj_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        # Symlink MEMORY.md
        ensure_symlink(memory_dir / "MEMORY.md", MEMORY_MD_PATH)
        # Symlink working/
        ensure_symlink(memory_dir / "working", WORKING_DIR)


def _register_hooks():
    """Register Meme hooks in Claude Code settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return

    try:
        settings = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        return

    hooks = settings.get("hooks", {})

    session_start_hook = {
        "matcher": "startup|resume|clear",
        "hooks": [{
            "type": "command",
            "command": str(BIN_DIR / "meme-session-start.sh"),
            "timeout": 10,
            "statusMessage": "Loading Meme working memory...",
        }],
    }

    query_hook = {
        "matcher": "*",
        "hooks": [{
            "type": "command",
            "command": str(BIN_DIR / "meme-query.sh"),
            "timeout": 15,
            "statusMessage": "Querying Meme...",
        }],
    }

    session_end_hook = {
        "matcher": "clear|logout|prompt_input_exit",
        "hooks": [{
            "type": "command",
            "command": str(BIN_DIR / "meme-session-end.sh"),
            "timeout": 30,
            "statusMessage": "Saving Meme session state...",
        }],
    }

    # Merge hooks (don't overwrite existing ones)
    for event, hook_config in [
        ("SessionStart", session_start_hook),
        ("UserPromptSubmit", query_hook),
        ("SessionEnd", session_end_hook),
    ]:
        existing = hooks.get(event, [])
        # Check if meme hook already registered
        meme_registered = any(
            any("meme" in h.get("command", "") for h in cfg.get("hooks", []))
            for cfg in existing
        )
        if not meme_registered:
            existing.append(hook_config)
        hooks[event] = existing

    settings["hooks"] = hooks
    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False))

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
    marker = "# meme-memory-system"
    for rc_name in [".zshrc", ".bash_profile", ".profile"]:
        rc_file = Path.home() / rc_name
        if not rc_file.exists():
            continue
        try:
            lines = rc_file.read_text(encoding="utf-8").splitlines()
            new_lines = []
            skip_next = False
            for line in lines:
                if marker in line:
                    skip_next = True  # skip the marker line itself
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

    memories = []
    for p in find_all_memories(include_cold=True, include_forgotten=show_forgotten):
        try:
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
        except Exception:
            continue

    # Sort
    if sort_by == "importance":
        memories.sort(key=lambda x: -x["importance"])
    elif sort_by == "recent":
        memories.sort(key=lambda x: x["last_accessed"], reverse=True)
    elif sort_by == "heat":
        memories.sort(key=lambda x: -x["access_count"])

    if not memories:
        print("No memories found.")
        return

    # Display
    for m in memories:
        tags_str = f" [{', '.join(m['tags'][:3])}]" if m['tags'] else ""
        print(f"  {m['id']}  imp={m['importance']:.1f}  tier={m['tier']}  "
              f"type={m['type']}{tags_str}")
        print(f"    {m['summary']}")
    print(f"\nTotal: {len(memories)} memories")

# ========================================
# Command: search
# ========================================

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
                        "type": entry.get("type", "sensitive"),
                        "importance": 0.5,
                        "tier": "vault",
                        "score": score,
                        "path": str(p),
                        "summary": entry.get("summary", "[encrypted]")[:120],
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
                    "type": meta.get("type", "unknown"),
                    "importance": meta.get("importance", 0.5),
                    "tier": tier,
                    "score": score,
                    "path": str(p),
                    "summary": body[:120].replace("\n", " "),
                })
        except Exception:
            continue

    results.sort(key=lambda x: -x["score"])

    if not results:
        print(f'No memories found for "{args.query}".')
        return

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

    if args.url:
        print(f"Fetching: {args.url}")
        try:
            resp = req.get(args.url, timeout=30)
            resp.raise_for_status()
            content = resp.text
            # Strip HTML tags for a rough text extraction
            content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
            content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()
            source_url = args.url
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
        except Exception:
            continue
    return related[:10]  # Cap at 10 links

# ========================================
# Command: import
# ========================================

def cmd_import(args):
    """Import memories from external sources."""
    sources = args.source  # list of sources
    for src in sources:
        if src == "claude":
            _do_import_claude()
        elif src == "claude-global":
            _do_import_claude_global()
        elif src == "codex":
            _do_import_codex(getattr(args, "path", None))
        else:
            print(f"Unknown source: {src}")


def _do_import_claude():
    """Import from Claude Code project memories."""
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        print("No Claude Code projects found.")
        return

    imported = 0
    for proj_dir in claude_projects.iterdir():
        if not proj_dir.is_dir():
            continue
        memory_dir = proj_dir / "memory"
        if not memory_dir.exists():
            continue

        project_name = proj_dir.name.lstrip("-").replace("-", "_")
        # Try to extract a cleaner project name
        parts = project_name.split("_")
        project_name = parts[-1] if parts else project_name

        for md_file in memory_dir.glob("*.md"):
            if md_file.name == "MEMORY.md":
                continue
            try:
                text = md_file.read_text(encoding="utf-8")
                meta, body = parse_frontmatter(text)

                # Determine type from filename prefix
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

                # Generate ID if missing
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

                # Check for duplicate
                existing = find_memory_by_id(meta["id"])
                if existing:
                    continue

                # Save to archive
                tier = get_tier(meta)
                mem_dir = get_memory_dir(mem_type, tier)
                mem_path = mem_dir / f"{meta['id']}.md"
                save_memory(mem_path, meta, body)
                _update_index_entry(meta["id"], meta, mem_path)
                imported += 1
            except Exception as e:
                print(f"  Failed to import {md_file}: {e}")

    if imported:
        rebuild_memory_md()
        git_commit(f"import: {imported} memories from Claude Code")
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
        except Exception:
            continue

    if imported:
        rebuild_memory_md()
        git_commit(f"import: {imported} memories from Codex")
    print(f"Imported {imported} memories from Codex.")

# ========================================
# Command: decay
# ========================================

def cmd_decay(args):
    """Run importance decay scan."""
    dry_run = args.dry_run or False
    now = datetime.date.today()
    decayed = 0

    for p in find_all_memories(include_cold=True):
        try:
            meta, body = load_memory(p)
            if meta.get("forgotten"):
                continue

            last = meta.get("last_accessed", meta.get("created", ""))
            if not last:
                continue
            last_date = datetime.date.fromisoformat(last)
            days = (now - last_date).days
            if days <= 0:
                continue

            old_imp = meta.get("importance", 0.5)
            # Correction memories decay slower
            decay_rate = 0.975 if meta.get("type") == "correction" else 0.95
            new_imp = old_imp * (decay_rate ** days)
            new_imp = round(new_imp, 4)

            if new_imp != old_imp:
                if not dry_run:
                    meta["importance"] = new_imp
                    save_memory(p, meta, body)
                    _update_index_entry(meta["id"], meta, p)

                    # Auto-migrate tiers
                    old_tier = get_tier({"importance": old_imp})
                    new_tier = get_tier({"importance": new_imp})
                    if old_tier != new_tier:
                        new_dir = get_memory_dir(meta.get("type", "feedback"), new_tier)
                        new_path = new_dir / p.name
                        if not new_path.exists():
                            p.rename(new_path)

                    # Log decay
                    with open(DECAY_LOG_PATH, "a") as f:
                        f.write(json.dumps({
                            "ts": datetime.datetime.now().isoformat(),
                            "id": meta["id"],
                            "old": old_imp,
                            "new": new_imp,
                            "days": days,
                        }) + "\n")

                decayed += 1
                print(f"  {meta['id']}: {old_imp:.3f} -> {new_imp:.3f} ({days} days)")

        except Exception:
            continue

    if not dry_run and decayed:
        rebuild_memory_md()
        git_commit(f"decay: {decayed} memories updated")

    action = "Would decay" if dry_run else "Decayed"
    print(f"\n{action} {decayed} memories.")

# ========================================
# Command: promote / demote / warm
# ========================================

def cmd_promote(args):
    """Manually promote a memory to working tier."""
    mem_id = args.id
    path = find_memory_by_id(mem_id)
    if not path:
        print(f"Memory not found: {mem_id}")
        return
    meta, body = load_memory(path)
    meta["importance"] = max(meta.get("importance", 0.5), TIER_WORKING_THRESHOLD)
    new_dir = get_memory_dir(meta.get("type", "feedback"), "working")
    new_path = new_dir / path.name
    if new_path != path:
        if new_path.exists():
            print(f"Target already exists: {new_path}")
            return
        path.rename(new_path)
        save_memory(new_path, meta, body)
        _update_index_entry(mem_id, meta, new_path)
    else:
        save_memory(path, meta, body)
        _update_index_entry(mem_id, meta, path)
    rebuild_memory_md()
    git_commit(f"promote: {mem_id}")
    print(f"Promoted {mem_id} to working tier.")


def cmd_demote(args):
    """Manually demote a memory."""
    mem_id = args.id
    path = find_memory_by_id(mem_id)
    if not path:
        print(f"Memory not found: {mem_id}")
        return
    meta, body = load_memory(path)
    new_importance = args.importance or max(meta.get("importance", 0.5) - 0.2, 0.05)
    meta["importance"] = new_importance
    new_tier = get_tier(meta)
    new_dir = get_memory_dir(meta.get("type", "feedback"), new_tier)
    new_path = new_dir / path.name
    if new_path != path and not new_path.exists():
        path.rename(new_path)
        save_memory(new_path, meta, body)
        _update_index_entry(mem_id, meta, new_path)
    else:
        save_memory(path, meta, body)
        _update_index_entry(mem_id, meta, path)
    rebuild_memory_md()
    git_commit(f"demote: {mem_id}")
    print(f"Demoted {mem_id} to {new_tier} (importance={new_importance:.2f}).")


def cmd_warm(args):
    """Warm a cold memory back to archive."""
    mem_id = args.id
    path = find_memory_by_id(mem_id)
    if not path:
        print(f"Memory not found: {mem_id}")
        return
    meta, body = load_memory(path)
    meta["importance"] = max(meta.get("importance", 0.1), 0.25)
    new_dir = get_memory_dir(meta.get("type", "feedback"), "archive")
    new_path = new_dir / path.name
    if new_path != path and not new_path.exists():
        path.rename(new_path)
        save_memory(new_path, meta, body)
        _update_index_entry(mem_id, meta, new_path)
    else:
        save_memory(path, meta, body)
        _update_index_entry(mem_id, meta, path)
    rebuild_memory_md()
    git_commit(f"warm: {mem_id}")
    print(f"Warmed {mem_id} to archive (importance={meta['importance']:.2f}).")

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

    # Check frontmatter
    for p in find_all_memories(include_cold=True):
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
        except Exception:
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
        try:
            meta, body = load_memory(p)
            mem_id = meta.get("id")
            if not mem_id:
                continue
            _update_index_entry(mem_id, meta, p, index=index)
            links = meta.get("links", [])
            if links:
                graph[mem_id] = links
        except Exception:
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
        except Exception:
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
        try:
            meta, body = load_memory(p)
            if meta.get("forgotten"):
                continue
            memories.append({"meta": meta, "body": body})
        except Exception:
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

# ========================================
# Command: heat
# ========================================

def cmd_heat(args):
    """Show current session heat map."""
    if not SESSION_HEAT_PATH.exists():
        print("No active session heat data.")
        return
    data = json.loads(SESSION_HEAT_PATH.read_text())
    heat_map = data.get("heat_map", {})
    if not heat_map:
        print("No memories heated this session.")
        return
    print(f"Session: {data.get('session_id', 'unknown')}")
    print(f"Started: {data.get('started', 'unknown')}\n")
    for mid, info in sorted(heat_map.items(), key=lambda x: -x[1].get("heat", 0)):
        print(f"  {mid}: heat={info.get('heat', 0):.2f}")

# ========================================
# Command: auth (biometric-gated secret access)
# ========================================

def cmd_auth(args):
    """Authenticate and export a vault secret as an environment variable."""
    mem_id = args.mem_id
    var_name = args.var or "MEM_SECRET"

    # Find the memory
    mem_path = find_memory_by_id(mem_id)
    if not mem_path:
        print(f"echo 'ERROR: Memory {mem_id} not found' >&2", file=sys.stderr)
        sys.exit(1)

    # Must be a vault memory
    if mem_path.suffix != ".enc":
        print(f"echo 'ERROR: {mem_id} is not a sensitive (vault) memory' >&2",
              file=sys.stderr)
        sys.exit(1)

    # Auth: retrieving the vault key triggers OS-level auth (Touch ID / Hello / password)
    try:
        key = _get_vault_key()
    except Exception as e:
        print(f"echo 'ERROR: Authentication failed — {e}' >&2", file=sys.stderr)
        sys.exit(1)

    # Decrypt
    try:
        meta, body = load_vault_memory(mem_id)
    except Exception as e:
        print(f"echo 'ERROR: Decryption failed — {e}' >&2", file=sys.stderr)
        sys.exit(1)

    if not body:
        print(f"echo 'ERROR: Memory {mem_id} is empty' >&2", file=sys.stderr)
        sys.exit(1)

    # Output export statement to stdout (consumed by source)
    escaped = body.replace("'", "'\\''")
    print(f"export {var_name}='{escaped}'")

# ========================================
# Command: version / upgrade / changelog
# ========================================

def cmd_version(args):
    """Show version info."""
    print(f"Meme v{CURRENT_VERSION}")
    if VERSION_PATH.exists():
        data = json.loads(VERSION_PATH.read_text())
        print(f"  Installed: {data.get('installed_at', 'unknown')}")
        print(f"  Schema: v{data.get('schema_version', '?')}")

def _check_remote_version(timeout=5):
    """Check for the latest published version.

    Strategy: PyPI first (for uvx/pipx users), then GitHub tags fallback
    (for install.sh users who may not publish to PyPI).
    Returns the latest version string if newer than CURRENT_VERSION, else None.
    """
    import urllib.request
    import urllib.error

    # --- Try PyPI ---
    try:
        url = "https://pypi.org/pypi/pymeme/json"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            ver = data.get("info", {}).get("version", "")
            if ver and _version_tuple(ver) > _version_tuple(CURRENT_VERSION):
                return ver
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        pass

    # --- Fallback: GitHub tags ---
    try:
        url = "https://api.github.com/repos/hyooeewee/Meme/tags?per_page=20"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            tags = json.loads(resp.read().decode())
            best = _version_tuple(CURRENT_VERSION)
            best_str = None
            for t in tags:
                name = t.get("name", "").lstrip("v")
                if _version_tuple(name) > best:
                    best = _version_tuple(name)
                    best_str = name
            if best_str:
                return best_str
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        pass

    return None


def _version_tuple(v):
    """Parse 'x.y.z' into (x, y, z) for comparison."""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


def cmd_upgrade(args):
    """Check for upgrades or perform upgrade."""
    if getattr(args, "check", False):
        latest = _check_remote_version()
        if latest:
            print(f"New version available: {CURRENT_VERSION} -> {latest}")
            # Cache the result
            VERSION_CHECK_PATH.write_text(json.dumps({
                "latest": latest,
                "current": CURRENT_VERSION,
                "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }, indent=2))
        else:
            print(f"Meme {CURRENT_VERSION} is up to date.")
        return

    # Full upgrade
    print(f"Meme v{CURRENT_VERSION}")
    # Check for latest version first
    latest = _check_remote_version()
    if not latest:
        print("Already up to date.")
        return

    print(f"New version available: {CURRENT_VERSION} -> {latest}")
    print()
    # Determine install method
    pkg_dir = MEME_HOME / "pkg"
    if (pkg_dir / ".git").exists():
        # Installed via install.sh (git clone)
        print("Upgrading via git pull...")
        result = git_run("-C", str(pkg_dir), "pull", "--ff-only", check=False)
        if result.returncode == 0:
            print("Updated. Re-installing CLI...")
            cli_src = pkg_dir / "meme-cli"
            cli_dst = BIN_DIR / "meme"
            if cli_src.exists():
                shutil.copy2(cli_src, cli_dst)
                cli_dst.chmod(0o755)
            for hook_file in ["session_start.sh", "query.sh", "session_end.sh"]:
                src = pkg_dir / "hooks" / hook_file
                dst = BIN_DIR / f"meme-{hook_file.replace('_', '-')}"
                if src.exists() and dst.is_symlink():
                    shutil.copy2(src, dst)
                    dst.chmod(0o755)
            VERSION_CHECK_PATH.write_text(json.dumps({
                "latest": latest,
                "current": CURRENT_VERSION,
                "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }, indent=2))
            print(f"Upgraded to {latest}.")
        else:
            print("git pull failed. Try manually:")
            print(f"  cd {pkg_dir} && git pull --ff-only")
    else:
        # Installed via uvx/pipx or other method
        print("To upgrade, run one of:")
        print(f"  uvx pymeme@{latest} install")
        print(f"  pipx run pymeme install")
        print(f"  curl -sSL https://raw.githubusercontent.com/hyooeewee/Meme/main/install.sh | bash")

def cmd_changelog(args):
    """Show changelog."""
    print("Changelog: see git log for changes.")
    if (MEME_HOME / ".git").exists():
        result = git_run("log", "--oneline", "-20", check=False)
        if result.stdout:
            print(result.stdout)

# ========================================
# Index & Graph helpers
# ========================================

def _update_index_entry(mem_id: str, meta: dict, path: Path, index: dict | None = None):
    """Update a single entry in the index."""
    if index is None:
        index = load_index()
    index[mem_id] = {
        "type": meta.get("type"),
        "importance": meta.get("importance"),
        "tags": meta.get("tags", []),
        "path": str(path),
        "last_accessed": meta.get("last_accessed"),
        "forgotten": meta.get("forgotten", False),
    }
    save_index(index)


def _remove_from_index(mem_id: str):
    index = load_index()
    index.pop(mem_id, None)
    save_index(index)


def _add_to_graph(mem_id: str, links: list[str]):
    """Add links to the graph (bidirectional)."""
    graph = load_graph()
    existing = set(graph.get(mem_id, []))
    for link in links:
        existing.add(link)
        # Bidirectional
        reverse = set(graph.get(link, []))
        reverse.add(mem_id)
        graph[link] = list(reverse)
    graph[mem_id] = list(existing)
    save_graph(graph)


def _remove_from_graph(mem_id: str):
    """Remove a node from the graph."""
    graph = load_graph()
    links = graph.pop(mem_id, [])
    for link in links:
        if link in graph:
            graph[link] = [l for l in graph[link] if l != mem_id]
    save_graph(graph)

# ========================================
# CLI Argument Parser
# ========================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meme",
        description="Meme — A centralized, tiered memory system with knowledge graph.",
    )
    sub = parser.add_subparsers(dest="command")

    # install
    p = sub.add_parser("install", help="Install the Meme system")
    p.add_argument("--migrate", action="store_true", help="Also import from Claude Code")
    p.add_argument("--obsidian", type=str, help="Path to Obsidian vault for symlink")
    p.set_defaults(func=cmd_install)

    # uninstall
    p = sub.add_parser("uninstall", help="Uninstall Meme")
    p.add_argument("--keep-data", action="store_true", help="Keep ~/.meme/ data")
    p.set_defaults(func=cmd_uninstall)

    # add
    p = sub.add_parser("add", help="Add a new memory")
    p.add_argument("content", help="Memory content")
    p.add_argument("--type", "-t", default="feedback",
                   choices=["feedback", "project", "user", "reference", "knowledge", "correction"])
    p.add_argument("--importance", "-i", type=float, default=0.6)
    p.add_argument("--tags", default="")
    p.add_argument("--links", default="")
    p.add_argument("--slug", default="")
    p.add_argument("--sensitive", action="store_true")
    p.add_argument("--source-url", default=None)
    p.add_argument("--source-file", default=None)
    p.add_argument("--corrects", default=None)
    p.add_argument("--scope", default=None)
    p.add_argument("--wrong-pattern", default=None)
    p.add_argument("--correct-pattern", default=None)
    p.set_defaults(func=cmd_add)

    # list
    p = sub.add_parser("list", help="List memories")
    p.add_argument("--tier", choices=["working", "archive", "cold"])
    p.add_argument("--tag", default=None)
    p.add_argument("--sort", default="importance", choices=["importance", "recent", "heat"])
    p.add_argument("--forgotten", action="store_true")
    p.set_defaults(func=cmd_list)

    # search
    p = sub.add_parser("search", help="Search memories by keyword")
    p.add_argument("query", help="Search query")
    p.set_defaults(func=cmd_search)

    # query
    p = sub.add_parser("query", help="Graph traversal retrieval")
    p.add_argument("id", help="Memory ID to start traversal from")
    p.set_defaults(func=cmd_query)

    # edit
    p = sub.add_parser("edit", help="Edit a memory")
    p.add_argument("id", help="Memory ID")
    p.add_argument("--content", default=None)
    p.add_argument("--importance", type=float, default=None)
    p.add_argument("--type", default=None)
    p.add_argument("--tags", default=None)
    p.add_argument("--add-link", default=None)
    p.set_defaults(func=cmd_edit)

    # delete
    p = sub.add_parser("delete", help="Delete a memory")
    p.add_argument("id", help="Memory ID")
    p.add_argument("--force", "-f", action="store_true")
    p.set_defaults(func=cmd_delete)

    # forget
    p = sub.add_parser("forget", help="Forget a memory")
    p.add_argument("id", help="Memory ID")
    p.add_argument("--hard", action="store_true", help="Hard delete")
    p.add_argument("--purge", action="store_true", help="Purge from git history")
    p.add_argument("--reason", default=None)
    p.set_defaults(func=cmd_forget)

    # learn
    p = sub.add_parser("learn", help="Learn from URL or file")
    p.add_argument("--url", default=None)
    p.add_argument("--file", default=None)
    p.add_argument("--slug", default="")
    p.add_argument("--importance", type=float, default=0.5)
    p.add_argument("--tags", default="")
    p.set_defaults(func=cmd_learn)

    # import
    p = sub.add_parser("import", help="Import memories from external sources")
    p.add_argument("source", nargs="+", choices=["claude", "claude-global", "codex"])
    p.add_argument("--path", default=None, help="Codex workspace path")
    p.set_defaults(func=cmd_import)

    # decay
    p = sub.add_parser("decay", help="Run importance decay scan")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_decay)

    # promote
    p = sub.add_parser("promote", help="Promote a memory to working tier")
    p.add_argument("id", help="Memory ID")
    p.set_defaults(func=cmd_promote)

    # demote
    p = sub.add_parser("demote", help="Demote a memory")
    p.add_argument("id", help="Memory ID")
    p.add_argument("--importance", type=float, default=None)
    p.set_defaults(func=cmd_demote)

    # warm
    p = sub.add_parser("warm", help="Warm a cold memory to archive")
    p.add_argument("id", help="Memory ID")
    p.set_defaults(func=cmd_warm)

    # link
    p = sub.add_parser("link", help="Link two memories")
    p.add_argument("id_a", help="First memory ID")
    p.add_argument("id_b", help="Second memory ID")
    p.set_defaults(func=cmd_link)

    # suggest-links
    p = sub.add_parser("suggest-links", help="Suggest new links")
    p.set_defaults(func=cmd_suggest_links)

    # doctor
    p = sub.add_parser("doctor", help="Health check")
    p.add_argument("--fix", action="store_true", help="Auto-fix issues")
    p.add_argument("--ask", action="store_true", help="Confirm each fix")
    p.set_defaults(func=cmd_doctor)

    # backup
    p = sub.add_parser("backup", help="Create a backup")
    p.set_defaults(func=cmd_backup)

    # gc
    p = sub.add_parser("gc", help="Clean old backups")
    p.set_defaults(func=cmd_gc)

    # reindex
    p = sub.add_parser("reindex", help="Rebuild index and graph")
    p.set_defaults(func=cmd_reindex)

    # stats
    p = sub.add_parser("stats", help="Show statistics")
    p.set_defaults(func=cmd_stats)

    # export
    p = sub.add_parser("export", help="Export all memories")
    p.add_argument("--format", default="json", choices=["json", "md"])
    p.add_argument("--output", "-o", default=None)
    p.set_defaults(func=cmd_export)

    # heat
    p = sub.add_parser("heat", help="Show session heat map")
    p.set_defaults(func=cmd_heat)

    # auth
    p = sub.add_parser("auth", help="Authenticate and export a vault secret")
    p.add_argument("mem_id", help="Vault memory ID to authenticate")
    p.add_argument("--var", default=None,
                   help="Environment variable name (default: MEM_SECRET)")
    p.set_defaults(func=cmd_auth)

    # version
    p = sub.add_parser("version", help="Show version")
    p.set_defaults(func=cmd_version)

    # upgrade
    p = sub.add_parser("upgrade", help="Upgrade Meme")
    p.add_argument("--check", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_upgrade)

    # changelog
    p = sub.add_parser("changelog", help="Show changelog")
    p.set_defaults(func=cmd_changelog)

    return parser

# ========================================
# Main
# ========================================

def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
