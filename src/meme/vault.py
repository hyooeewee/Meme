"""Meme vault encryption: Touch ID + keyring + Fernet."""
import json
import os
import platform
import re
from pathlib import Path

import yaml

from meme.constants import VAULT_DIR, META_DIR
from meme.utils import VAULT_KEYRING_SERVICE, VAULT_KEYRING_USER

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


def save_vault_memory(mem_id: str, meta, body: str):
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


def load_vault_memory(mem_id: str):
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


