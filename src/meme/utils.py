"""Meme utility functions: frontmatter, file discovery, git, index/graph."""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
from collections import deque
from pathlib import Path

import yaml


def _get_package_resource_path(relative_path: str):
    """Get a path to a package resource, works in both package and script mode."""
    try:
        from importlib.resources import files
        ref = files("meme").joinpath(relative_path)
        p = Path(str(ref))
        if p.exists():
            return p
    except (ModuleNotFoundError, TypeError, FileNotFoundError):
        pass
    fallback = Path(__file__).resolve().parent / relative_path
    if fallback.exists():
        return fallback
    fallback2 = Path(__file__).resolve().parent.parent / "hooks" / Path(relative_path).name
    if relative_path.startswith("hooks/") and fallback2.exists():
        return fallback2
    return None


from meme.constants import (
    MEME_HOME, WORKING_DIR, ARCHIVE_DIR, COLD_DIR, VAULT_DIR,
    BACKUPS_DIR, META_DIR, BIN_DIR, INDEX_PATH, GRAPH_PATH,
    VERSION_PATH, IMPORT_STATE_PATH, SESSION_HEAT_PATH,
    CONFLICT_LOG_PATH, DECAY_LOG_PATH, FORGOTTEN_INDEX_PATH,
    VERSION_CHECK_PATH, MEMORY_MD_PATH, CURRENT_SCHEMA,
    TOKEN_BUDGET_WORKING, TOKEN_BUDGET_HOOK,
    TIER_WORKING_THRESHOLD, TIER_ARCHIVE_THRESHOLD,
    SUBDIRS, FRONTMATTER_KEYS,
)
from meme.config import DEFAULT_CONFIG


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
# Index & Graph helpers
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

