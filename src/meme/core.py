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


# ========================================
# Version — single source of truth in pyproject.toml
# ========================================

from meme import __version__ as CURRENT_VERSION
import os
import re
import shutil
import subprocess
import sys
import tarfile
from collections import deque
from pathlib import Path

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

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
    # Fallback: relative to this file (script mode, e.g. `uv run meme`)
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

MEME_HOME = Path(os.environ.get("MEME_HOME", str(Path.home() / ".meme")))
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
# Configuration
# ========================================

CONFIG_PATH = MEME_HOME / "config.toml"

DEFAULT_CONFIG: dict = {
    "dream": {
        "enabled": True,
        "schedule": "0 3 * * *",
        "threshold": 0.4,
        "auto_apply": True,
        "mode": "all",
        "report_dir": "dreams",
    },
    "daydream": {
        "threshold": 0.4,
        "default_mode": "all",
    },
    "hooks": {
        "session_end_check_dream": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config() -> dict:
    """Load user config merged with defaults."""
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            text = CONFIG_PATH.read_text(encoding="utf-8")
            user = tomllib.loads(text)
            config = _deep_merge(config, user)
        except Exception:
            pass
    return config


def save_config(config: dict):
    """Save config to disk (preserving comments is not supported)."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Meme configuration", ""]
    for section, values in config.items():
        lines.append(f"[{section}]")
        for key, val in values.items():
            if isinstance(val, bool):
                lines.append(f'{key} = {str(val).lower()}')
            elif isinstance(val, str):
                lines.append(f'{key} = "{val}"')
            elif isinstance(val, (int, float)):
                lines.append(f'{key} = {val}')
            elif isinstance(val, list):
                items = ', '.join(f'"{v}"' for v in val)
                lines.append(f'{key} = [{items}]')
        lines.append("")
    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")


def get_config_value(config: dict, key_path: str):
    """Get a config value by dot path, e.g. 'dream.enabled'."""
    keys = key_path.split(".")
    val = config
    for k in keys:
        if isinstance(val, dict) and k in val:
            val = val[k]
        else:
            return None
    return val


def set_config_value(config: dict, key_path: str, value) -> bool:
    """Set a config value by dot path. Returns True if successful."""
    keys = key_path.split(".")
    val = config
    for k in keys[:-1]:
        if k not in val:
            val[k] = {}
        val = val[k]
    # Type coercion based on existing value in defaults
    target_key = keys[-1]
    existing = get_config_value(DEFAULT_CONFIG, key_path)
    if existing is not None:
        if isinstance(existing, bool):
            value = value.lower() in ("true", "1", "yes", "on")
        elif isinstance(existing, (int, float)):
            try:
                value = float(value)
                if isinstance(existing, int):
                    value = int(value)
            except ValueError:
                return False
    val[target_key] = value
    return True


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
    # Vault .enc files: avoid decrypting just to check forgotten status
    if path.suffix == ".enc":
        return path.stem in forgotten_ids
    try:
        meta, _ = load_memory(path)
        return meta.get("id") in forgotten_ids or meta.get("forgotten")
    except Exception:
        return False


def find_memory_by_id(mem_id: str) -> Path | None:
    """Find a memory file by its ID."""
    for p in find_all_memories(include_cold=True):
        # Vault .enc files: avoid decrypting; the filename stem is the id
        if p.suffix == ".enc":
            if p.stem == mem_id:
                return p
            continue
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


def _touch_id_auth(reason: str = "Access Meme vault") -> bool:
    """Authenticate with Touch ID / Face ID on macOS. Returns True if authenticated."""
    import platform
    if platform.system() != "Darwin":
        return False
    try:
        from LocalAuthentication import LAContext
        import Foundation

        context = LAContext.alloc().init()
        avail, _ = context.canEvaluatePolicy_error_(1, None)
        if not avail:
            # Biometrics not available, fall back to device password
            avail, _ = context.canEvaluatePolicy_error_(2, None)
            if not avail:
                return False
            policy = 2  # LAPolicyDeviceOwnerAuthentication
        else:
            policy = 1  # LAPolicyDeviceOwnerAuthenticationWithBiometrics

        result = {"done": False, "success": False}

        def callback(success, error):
            result["done"] = True
            result["success"] = success
            result["error"] = error

        context.evaluatePolicy_localizedReason_reply_(policy, reason, callback)

        for _ in range(100):
            if result["done"]:
                break
            Foundation.NSRunLoop.currentRunLoop().runMode_beforeDate_(
                Foundation.NSDefaultRunLoopMode,
                Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1),
            )
        return result.get("success", False)
    except Exception:
        return False


def _get_vault_key(require_auth: bool = True) -> bytes:
    """Get or create the vault encryption key via OS keyring.

    When require_auth is True (default for reads), macOS Touch ID / password
    is prompted before accessing the keychain. When False (for writes),
    the key is retrieved silently so new vault items can be created without
    interrupting the user.
    """
    import platform
    if require_auth and platform.system() == "Darwin":
        # Prompt Touch ID / password before accessing keychain
        if not _touch_id_auth("Authenticate to access Meme vault"):
            # Touch ID cancelled or unavailable — still try keyring
            pass
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
    key = _get_vault_key(require_auth=False)
    f = Fernet(key)
    return f.encrypt(plaintext.encode("utf-8"))


def vault_decrypt(ciphertext: bytes) -> str:
    """Decrypt vault ciphertext."""
    from cryptography.fernet import Fernet
    key = _get_vault_key(require_auth=True)
    f = Fernet(key)
    return f.decrypt(ciphertext).decode("utf-8")


def save_vault_memory(mem_id: str, meta: dict, body: str):
    """Save an encrypted memory to the vault.

    Extracts the secret value from body for encryption, and the descriptive
    text for the plaintext index summary. This ensures the encrypted file
    contains only the secret (e.g. 'sk-live-xxx'), making it safe to inject
    directly as an environment variable via `meme auth` or `meme run`.
    """
    body = body.strip()

    # Try to separate description from secret value.
    # E.g. "我的 API token 是 sk-live-xxx" -> desc="我的 API token", secret="sk-live-xxx"
    # E.g. "API token: sk-live-xxx"        -> desc="API token",      secret="sk-live-xxx"
    m = re.search(r'^(.{3,200}?)\s*(?:是|为|:|：|=)\s*(.+)$', body)
    if m:
        description = m.group(1).strip()
        secret_value = m.group(2).strip()
    else:
        # No clear separator: treat entire body as secret.
        description = meta.get("type", "secret").upper()
        secret_value = body

    # Encrypt only the secret value (with frontmatter)
    full_content = save_memory_to_string(meta, secret_value)
    encrypted = vault_encrypt(full_content)
    enc_path = VAULT_DIR / f"{mem_id}.enc"
    enc_path.write_bytes(encrypted)
    # Write plaintext index entry (no secrets, just metadata for search)
    index_entry = {
        "id": mem_id,
        "type": meta.get("type", "knowledge"),
        "tags": meta.get("tags", []),
        "summary": description,
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
# Command: setup
# ========================================

def cmd_setup(args):
    """Set up the Meme system."""
    is_dev = getattr(args, "dev", False)
    already_installed = MEME_HOME.exists() and (MEME_HOME / "meta" / "version.json").exists()

    if already_installed and not is_dev:
        print(f"Meme is already set up at {MEME_HOME}")
        print("Use 'meme upgrade' to update, or 'meme uninstall' first.")
        return

    if already_installed and is_dev:
        print("Re-syncing hooks in dev mode...")
    else:
        print("Setting up Meme memory system...")

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

    is_dev = getattr(args, "dev", False)
    version_data = {
        "installed_version": CURRENT_VERSION,
        "installed_at": datetime.datetime.now().isoformat(),
        "schema_version": CURRENT_SCHEMA,
        "last_upgrade": None,
        "last_doctor": None,
        "dev": is_dev,
        "obsidian_path": None,
    }
    VERSION_PATH.write_text(json.dumps(version_data, indent=2))

    IMPORT_STATE_PATH.write_text("{}")
    CONFLICT_LOG_PATH.write_text("")
    DECAY_LOG_PATH.write_text("")

    # Write MEMORY.md
    rebuild_memory_md()

    # Install hook scripts (copy in prod, symlink in dev)
    for hook_file in ["session_start.sh", "query.sh", "session_end.sh"]:
        dst = BIN_DIR / f"meme-{hook_file.replace('_', '-')}"
        src = _get_package_resource_path(f"hooks/{hook_file}")
        if not src:
            continue
        if is_dev:
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            dst.symlink_to(src)
            print(f"  Dev hook: {dst} -> {src}")
        else:
            if dst.is_symlink():
                dst.unlink()
            elif dst.exists():
                continue
            shutil.copy2(src, dst)
            dst.chmod(0o755)

    # In dev mode, symlink the CLI entry point so `meme` works from PATH
    if is_dev:
        cli_dst = BIN_DIR / "meme"
        repo_root = Path(__file__).resolve().parent.parent.parent
        venv_meme = repo_root / ".venv" / "bin" / "meme"
        launcher = repo_root / "meme"
        if venv_meme.exists():
            if cli_dst.exists() or cli_dst.is_symlink():
                cli_dst.unlink()
            cli_dst.symlink_to(venv_meme)
            print(f"  Dev CLI: {cli_dst} -> {venv_meme}")
        elif launcher.exists():
            if cli_dst.exists() or cli_dst.is_symlink():
                cli_dst.unlink()
            cli_dst.symlink_to(launcher)
            print(f"  Dev CLI: {cli_dst} -> {launcher}")

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
            # Record obsidian path in version.json for uninstall
            try:
                vd = json.loads(VERSION_PATH.read_text())
                vd["obsidian_path"] = str(obsidian_target)
                VERSION_PATH.write_text(json.dumps(vd, indent=2))
            except Exception:
                pass

    # Initial commit
    git_commit("init: meme memory system installed")

    # Add to PATH if not already in shell rc file
    _setup_path(str(BIN_DIR))

    print(f"\nMeme set up successfully at {MEME_HOME}")
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

    # --- Dream (launchd) setup ---
    dream_install = getattr(args, "dream", False)
    dream_reload = getattr(args, "dream_reload", False)
    if dream_install or dream_reload:
        import platform
        if platform.system() != "Darwin":
            print("Dream launchd setup is only supported on macOS.")
            print("Use cron on Linux: add '0 3 * * * meme dream' to your crontab")
        else:
            config = load_config()
            schedule = config.get("dream", {}).get("schedule", "0 3 * * *")
            plist_content = _generate_launchd_plist(schedule)
            launch_agents = Path.home() / "Library" / "LaunchAgents"
            launch_agents.mkdir(parents=True, exist_ok=True)
            plist_path = launch_agents / "com.meme.dream.plist"

            # Write plist
            plist_path.write_text(plist_content, encoding="utf-8")

            # Load/unload
            subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
            result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
            if result.returncode == 0:
                print(f"Dream launchd job installed: {plist_path}")
                print(f"  Schedule: {schedule}")
                print(f"  Logs: {MEME_HOME}/dreams/dream.log")
            else:
                print(f"Failed to load launchd job: {result.stderr}")

    if not dream_install and not dream_reload and not already_installed:
        print("\nTip: Enable nightly dream consolidation with:")
        print("  meme setup --dream")

# ========================================
# Command: init
# ========================================

CLAUDE_MD_TEMPLATE = """# Meme — Project Memory System

This project uses [Meme](https://github.com/hyooeewee/Meme) for centralized memory management.

## Quick Reference

| Command | Purpose |
|---------|---------|
| `meme add "content"` | Add a new memory |
| `meme search "keyword"` | Search memories |
| `meme list` | List all memories |
| `meme edit <id>` | Edit a memory |
| `meme link <id_a> <id_b>` | Link two memories |

## Project Memory

- Project memory file: `~/.meme/archive/projects/{project_safe_name}.md`
- Working memories: `~/.meme/working/` (always loaded)
- Archive memories: `~/.meme/archive/` (graph traversal)

## For AI Assistants

When working in this project:
1. **SessionStart**: Working memories are auto-loaded via hook
2. **UserPromptSubmit**: Relevant archive memories are auto-injected via keyword search
3. **SessionEnd**: Access counts and heat are persisted

Use `[[mem_id]]` syntax to reference memories. Create links between related memories to build the knowledge graph.
"""


def cmd_init(args):
    """Initialize Meme integration in the current project directory."""
    if not MEME_HOME.exists():
        print("Meme is not set up yet. Run 'meme setup' first.")
        return

    project_name = Path.cwd().name
    safe_name = re.sub(r"[^\w-]", "_", project_name).lower()

    # 1. Create .claude/ directory
    claude_dir = Path.cwd() / ".claude"
    claude_dir.mkdir(exist_ok=True)

    # Ensure .claude/.gitignore exists (ignore all except memory/)
    gitignore = claude_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")

    # 2. Create .claude/memory/ and symlinks
    memory_dir = claude_dir / "memory"
    memory_dir.mkdir(exist_ok=True)
    ensure_symlink(memory_dir / "MEMORY.md", MEMORY_MD_PATH)
    ensure_symlink(memory_dir / "working", WORKING_DIR)

    # 3. Create/update CLAUDE.md
    claude_md = Path.cwd() / "CLAUDE.md"
    meme_section = CLAUDE_MD_TEMPLATE.format(project_safe_name=safe_name)

    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if "# Meme — Project Memory System" in content:
            print("  CLAUDE.md already has Meme section. Skipping.")
        else:
            content = content.rstrip() + "\n\n" + meme_section
            claude_md.write_text(content, encoding="utf-8")
            print("  Updated CLAUDE.md with Meme section.")
    else:
        claude_md.write_text(meme_section, encoding="utf-8")
        print("  Created CLAUDE.md with Meme guide.")

    # 4. Create project memory file
    project_mem_path = ARCHIVE_DIR / "projects" / f"{safe_name}.md"
    project_mem_path.parent.mkdir(parents=True, exist_ok=True)
    if not project_mem_path.exists():
        now = datetime.datetime.now().strftime("%Y-%m-%d")
        meta = {
            "id": f"mem_{datetime.datetime.now():%Y%m%d}_{safe_name}",
            "type": "project",
            "importance": 0.7,
            "created": now,
            "last_accessed": now,
            "access_count": 0,
            "tags": [safe_name],
            "links": [],
        }
        body = f"# {project_name}\n\nProject memory for {project_name}.\n\n## Overview\n\n## Notes\n\n## Related\n"
        save_memory(project_mem_path, meta, body)
        print(f"  Created project memory: {project_mem_path}")
    else:
        print(f"  Project memory already exists: {project_mem_path}")

    # 5. Register hooks (idempotent — safe to call even if already registered)
    _register_hooks()

    # 6. Rebuild MEMORY.md index
    rebuild_memory_md()

    print(f"\nMeme initialized for project '{project_name}'")
    print("  Run 'meme --help' for available commands.")


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
        except Exception:
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
    mode = getattr(args, "mode", None) or dd_cfg.get("default_mode", "all")
    threshold = getattr(args, "threshold", None)
    if threshold is None:
        threshold = dd_cfg.get("threshold", 0.4)
    apply_links = getattr(args, "apply", False)

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

    # Sync graph and index
    if not dry_run and (clusters or link_suggestions):
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
    import io
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

    # Write secret to a secure temp file instead of stdout to keep it out of AI context
    import tempfile
    escaped = body.replace("'", "'\\''")
    fd, tmp_path = tempfile.mkstemp(prefix="memectl_secret_", suffix=".sh")
    os.chmod(tmp_path, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"export {var_name}='{escaped}'\n")
    # Output a command that sources the file and then removes it
    print(f"source '{tmp_path}' && rm -f '{tmp_path}'")

# ========================================
# Command: run (vault-gated command execution)
# ========================================

def cmd_run(args):
    """Decrypt a vault secret, inject it as an env var, and exec a command.

    The AI never sees the plaintext — it only references the variable name.
    Use single quotes around arguments that reference $VAR so the shell
    does not expand them before meme run sets the env var.

    Example:
        meme run mem_xxx --var API_TOKEN -- sh -c 'curl -H "Authorization: Bearer $API_TOKEN" https://api.example.com'
    """
    mem_id = args.mem_id
    var_name = args.var or "MEM_SECRET"
    cmd_list = args.cmd

    if not cmd_list:
        print("ERROR: No command provided after '--'", file=sys.stderr)
        sys.exit(1)

    # Find the memory
    mem_path = find_memory_by_id(mem_id)
    if not mem_path:
        print(f"ERROR: Memory {mem_id} not found", file=sys.stderr)
        sys.exit(1)

    if mem_path.suffix != ".enc":
        print(f"ERROR: {mem_id} is not a vault memory", file=sys.stderr)
        sys.exit(1)

    # Auth + decrypt (triggers OS-level Touch ID / password if needed)
    try:
        _get_vault_key()
    except Exception as e:
        print(f"ERROR: Authentication failed — {e}", file=sys.stderr)
        sys.exit(1)

    try:
        meta, body = load_vault_memory(mem_id)
    except Exception as e:
        print(f"ERROR: Decryption failed — {e}", file=sys.stderr)
        sys.exit(1)

    if not body:
        print(f"ERROR: Memory {mem_id} is empty", file=sys.stderr)
        sys.exit(1)

    # Inject into environment (AI never sees this value)
    os.environ[var_name] = body.strip()

    # Exec the target command — this replaces the current process
    # so the secret is never returned as output to the AI
    try:
        os.execvp(cmd_list[0], cmd_list)
    except FileNotFoundError:
        print(f"ERROR: Command not found: {cmd_list[0]}", file=sys.stderr)
        sys.exit(127)
    except Exception as e:
        print(f"ERROR: Failed to execute command — {e}", file=sys.stderr)
        sys.exit(1)


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

    Strategy: PyPI first, then GitHub tags fallback.
    Returns the latest version string if newer than CURRENT_VERSION, else None.
    """
    import urllib.request
    import urllib.error

    # --- Try PyPI ---
    try:
        url = "https://pypi.org/pypi/memectl/json"
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
    force = getattr(args, "force", False)
    # Check for latest version first
    latest = _check_remote_version()
    if not latest and not force:
        print("Already up to date.")
        return
    if force and not latest:
        latest = CURRENT_VERSION  # force reinstall current version

    print(f"New version available: {CURRENT_VERSION} -> {latest}")
    print()

    venv_dir = MEME_HOME / "venv"
    pkg_dir = MEME_HOME / "pkg"

    if venv_dir.exists():
        # Installed via install.sh (venv + pip)
        venv_pip = venv_dir / "bin" / "pip"
        if venv_pip.exists():
            print("Upgrading via pip in venv...")
            result = subprocess.run(
                [str(venv_pip), "install", "--upgrade", "memectl"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("Package updated. Refreshing hooks...")
                # Re-install hook scripts from package
                for hook_file in ["session_start.sh", "query.sh", "session_end.sh"]:
                    dst = BIN_DIR / f"meme-{hook_file.replace('_', '-')}"
                    if dst.is_symlink():
                        continue
                    src = _get_package_resource_path(f"hooks/{hook_file}")
                    if src:
                        shutil.copy2(src, dst)
                        dst.chmod(0o755)
                # Update version meta
                if VERSION_PATH.exists():
                    data = json.loads(VERSION_PATH.read_text())
                    data["installed_version"] = latest
                    data["last_upgrade"] = datetime.datetime.now().isoformat()
                    VERSION_PATH.write_text(json.dumps(data, indent=2))
                VERSION_CHECK_PATH.write_text(json.dumps({
                    "latest": latest,
                    "current": CURRENT_VERSION,
                    "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }, indent=2))
                git_commit(f"upgrade: v{latest}")
                print(f"Upgraded to {latest}.")
                print("  Run 'meme --version' to verify.")
            else:
                print("pip upgrade failed:")
                print(result.stderr or result.stdout)
                print("Try manually:")
                print(f"  {venv_pip} install --upgrade memectl")
        else:
            print(f"venv found but pip missing at {venv_pip}")
            print("To upgrade, re-run the installer:")
            print("  curl -sSL https://raw.githubusercontent.com/hyooeewee/Meme/main/install.sh | bash")
    elif (pkg_dir / ".git").exists():
        # Legacy: installed via old install.sh (git clone)
        print("Upgrading via git pull...")
        result = git_run("-C", str(pkg_dir), "pull", "--ff-only", check=False)
        if result.returncode == 0:
            print("Updated. Re-installing CLI...")
            cli_src = pkg_dir / "meme"
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
            if VERSION_PATH.exists():
                data = json.loads(VERSION_PATH.read_text())
                data["installed_version"] = latest
                data["last_upgrade"] = datetime.datetime.now().isoformat()
                VERSION_PATH.write_text(json.dumps(data, indent=2))
            VERSION_CHECK_PATH.write_text(json.dumps({
                "latest": latest,
                "current": CURRENT_VERSION,
                "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }, indent=2))
            git_commit(f"upgrade: v{latest}")
            print(f"Upgraded to {latest}.")
        else:
            print("git pull failed. Try manually:")
            print(f"  cd {pkg_dir} && git pull --ff-only")
    else:
        print("Unknown installation method.")
        print("To upgrade, re-run the installer:")
        print("  curl -sSL https://raw.githubusercontent.com/hyooeewee/Meme/main/install.sh | bash")

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
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {CURRENT_VERSION}",
    )
    sub = parser.add_subparsers(dest="command")

    # setup
    p = sub.add_parser("setup", help="Set up the Meme system")
    p.add_argument("--migrate", action="store_true", help="Also import from Claude Code")
    p.add_argument("--obsidian", type=str, help="Path to Obsidian vault for symlink")
    p.add_argument("--dev", action="store_true", help="Symlink hook scripts instead of copying (for local development)")
    p.add_argument("--dream", action="store_true", help="Install launchd plist for nightly dream consolidation (macOS)")
    p.add_argument("--dream-reload", action="store_true", help="Reload launchd job after config changes")
    p.set_defaults(func=cmd_setup)

    # init
    p = sub.add_parser("init", help="Init Meme in the current project (CLAUDE.md + .claude/)")
    p.set_defaults(func=cmd_init)

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
    p.add_argument("--format", default="text", choices=["text", "json"],
                   help="Output format (default: text)")
    p.set_defaults(func=cmd_list)

    # search
    # show
    p = sub.add_parser("show", help="Show a memory's full content")
    p.add_argument("id", help="Memory ID")
    p.set_defaults(func=cmd_show)

    # search
    p = sub.add_parser("search", help="Search memories by keyword")
    p.add_argument("query", help="Search query")
    p.add_argument("--format", default="text", choices=["text", "json"],
                   help="Output format (default: text)")
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
    p.add_argument("url", nargs="?", default=None, help="URL to learn from")
    p.add_argument("--url", dest="url_flag", default=None, help="URL to learn from (alternative)")
    p.add_argument("--file", default=None, help="Local file to learn from")
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

    # daydream
    p = sub.add_parser("daydream", help="Semantic clustering and link consolidation")
    p.add_argument("--dry-run", action="store_true", help="Preview without applying changes")
    p.add_argument("--mode", choices=["all", "cluster", "link"], default="all",
                   help="Run mode (default: all)")
    p.add_argument("--threshold", type=float, default=0.4,
                   help="Similarity threshold for clustering (default: 0.4)")
    p.add_argument("--apply", action="store_true",
                   help="Apply suggested links automatically")
    p.set_defaults(func=cmd_daydream)

    # config
    p = sub.add_parser("config", help="View or modify configuration")
    p.add_argument("--get", default=None, help="Get a config value by dot path (e.g. dream.enabled)")
    p.add_argument("--set", default=None, help="Set a config value (e.g. dream.enabled=false)")
    p.add_argument("--edit", action="store_true", help="Open config in $EDITOR")
    p.set_defaults(func=cmd_config)

    # dream
    p = sub.add_parser("dream", help="Run automated memory consolidation (night mode)")
    p.set_defaults(func=cmd_dream)

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

    # run
    p = sub.add_parser("run", help="Run a command with a vault secret as env var")
    p.add_argument("mem_id", help="Vault memory ID")
    p.add_argument("--var", default=None,
                   help="Environment variable name (default: MEM_SECRET)")
    p.add_argument("cmd", nargs="*",
                   help="Command to execute (after --)")
    p.set_defaults(func=cmd_run)

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
