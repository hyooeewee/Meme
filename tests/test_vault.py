# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///

"""Tests for Meme vault encryption/decryption."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ========================================
# Module-level setup: inject missing imports into meme.vault
# ========================================

import yaml

import meme.vault as _vault_mod

# vault.py uses json, re, yaml but doesn't import them
_vault_mod.json = json
_vault_mod.re = __import__("re")
_vault_mod.yaml = yaml

# vault.py uses VAULT_KEYRING_SERVICE / VAULT_KEYRING_USER but doesn't import them
from meme.utils import VAULT_KEYRING_SERVICE, VAULT_KEYRING_USER

_vault_mod.VAULT_KEYRING_SERVICE = VAULT_KEYRING_SERVICE
_vault_mod.VAULT_KEYRING_USER = VAULT_KEYRING_USER


# ========================================
# Fixtures
# ========================================

@pytest.fixture
def mock_keyring(monkeypatch):
    """Mock the keyring module for vault tests."""
    mock = MagicMock()
    mock.get_password.return_value = None
    mock.set_password.return_value = None
    monkeypatch.setitem(sys.modules, "keyring", mock)
    return mock


@pytest.fixture
def isolated_vault(init_meme, monkeypatch):
    """Ensure VAULT_DIR points to the isolated test directory."""
    import meme.constants as const
    import meme.vault as vault_mod

    vault_dir = init_meme / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(const, "VAULT_DIR", vault_dir)
    monkeypatch.setattr(vault_mod, "VAULT_DIR", vault_dir)
    return vault_dir


# ========================================
# Vault key and encryption tests
# ========================================

class TestVaultKey:
    def test_get_vault_key_generates_new_key(self, init_meme, mock_keyring):
        """Getting vault key when none exists should generate a new one."""
        from meme.vault import _get_vault_key

        key = _get_vault_key(require_auth=False)

        assert isinstance(key, bytes)
        assert len(key) > 0
        mock_keyring.set_password.assert_called_once()

    def test_get_vault_key_reuses_existing(self, init_meme, mock_keyring):
        """Getting vault key when one exists should reuse it."""
        from meme.vault import _get_vault_key

        existing_key = "existing_fernet_key_123"
        mock_keyring.get_password.return_value = existing_key

        key = _get_vault_key(require_auth=False)

        assert key == existing_key.encode("utf-8")
        mock_keyring.set_password.assert_not_called()

    def test_get_vault_key_with_auth_non_darwin(self, init_meme, mock_keyring):
        """On non-Darwin platforms, auth should be skipped."""
        from meme.vault import _get_vault_key

        mock_keyring.get_password.return_value = "test_key"
        with patch("meme.vault.platform.system", return_value="Linux"):
            key = _get_vault_key(require_auth=True)
            assert key == b"test_key"


class TestVaultEncryptDecrypt:
    def test_encrypt_decrypt_roundtrip(self, init_meme, mock_keyring):
        """Encrypt then decrypt should return original plaintext."""
        from meme.vault import vault_encrypt, vault_decrypt
        from cryptography.fernet import Fernet

        # Use a fixed key so encrypt and decrypt use the same key
        fixed_key = Fernet.generate_key()
        mock_keyring.get_password.return_value = fixed_key.decode()

        plaintext = "my secret API token: sk-live-12345"

        ciphertext = vault_encrypt(plaintext)
        assert isinstance(ciphertext, bytes)
        assert ciphertext != plaintext.encode("utf-8")

        decrypted = vault_decrypt(ciphertext)
        assert decrypted == plaintext

    def test_encrypt_different_ciphertexts(self, init_meme, mock_keyring):
        """Encrypting same plaintext twice should yield different ciphertexts."""
        from meme.vault import vault_encrypt
        from cryptography.fernet import Fernet

        # Use a fixed key for consistent testing
        fixed_key = Fernet.generate_key()
        mock_keyring.get_password.return_value = fixed_key.decode()

        plaintext = "same text"

        ct1 = vault_encrypt(plaintext)
        ct2 = vault_encrypt(plaintext)

        assert ct1 != ct2

    def test_decrypt_wrong_key_fails(self, init_meme):
        """Decrypting with wrong key should raise an error."""
        from cryptography.fernet import Fernet
        from meme.vault import vault_decrypt

        key1 = Fernet.generate_key()
        f1 = Fernet(key1)
        ciphertext = f1.encrypt(b"secret data")

        key2 = Fernet.generate_key()
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = key2.decode()

        with patch.dict(sys.modules, {"keyring": mock_keyring}):
            with pytest.raises(Exception):
                vault_decrypt(ciphertext)

    def test_encrypt_unicode_content(self, init_meme, mock_keyring):
        """Encrypt/decrypt should handle unicode content."""
        from meme.vault import vault_encrypt, vault_decrypt
        from cryptography.fernet import Fernet

        # Use a fixed key so encrypt and decrypt use the same key
        fixed_key = Fernet.generate_key()
        mock_keyring.get_password.return_value = fixed_key.decode()

        plaintext = "中文内容 日本語コンテンツ emojis"

        ciphertext = vault_encrypt(plaintext)
        decrypted = vault_decrypt(ciphertext)
        assert decrypted == plaintext


# ========================================
# Touch ID auth tests
# ========================================

class TestTouchIdAuth:
    def test_touch_id_non_darwin_returns_false(self, init_meme):
        """Touch ID on non-Darwin should return False."""
        from meme.vault import _touch_id_auth

        with patch("meme.vault.platform.system", return_value="Linux"):
            result = _touch_id_auth("test reason")
            assert result is False

    def test_touch_id_darwin_unavailable(self, init_meme):
        """Touch ID on Darwin when biometrics unavailable should try password fallback."""
        from meme.vault import _touch_id_auth

        with patch("meme.vault.platform.system", return_value="Darwin"):
            with patch("LocalAuthentication.LAContext") as mock_la:
                context = MagicMock()
                # Biometrics unavailable, password available
                context.canEvaluatePolicy_error_.side_effect = [
                    (False, None),  # biometrics not available
                    (True, None),   # device password available
                ]
                context.evaluatePolicy_localizedReason_reply_.side_effect = (
                    lambda policy, reason, callback: callback(True, None)
                )
                mock_la.alloc.return_value.init.return_value = context

                result = _touch_id_auth("test reason")
                assert result is True

    def test_touch_id_darwin_cancelled(self, init_meme):
        """Touch ID on Darwin when user cancels should return False."""
        from meme.vault import _touch_id_auth

        with patch("meme.vault.platform.system", return_value="Darwin"):
            with patch("LocalAuthentication.LAContext") as mock_la:
                context = MagicMock()
                context.canEvaluatePolicy_error_.side_effect = [
                    (True, None),  # biometrics available
                ]

                def delayed_callback(policy, reason, callback):
                    callback(False, None)

                context.evaluatePolicy_localizedReason_reply_.side_effect = delayed_callback
                mock_la.alloc.return_value.init.return_value = context

                result = _touch_id_auth("test reason")
                assert result is False

    def test_touch_id_import_error(self, init_meme):
        """Touch ID when LocalAuthentication import fails should return False."""
        from meme.vault import _touch_id_auth

        with patch("meme.vault.platform.system", return_value="Darwin"):
            with patch.dict("sys.modules", {"LocalAuthentication": None}):
                result = _touch_id_auth("test reason")
                assert result is False


# ========================================
# Save / Load vault memory tests
# ========================================

class TestSaveLoadVaultMemory:
    def test_save_vault_memory_creates_enc_file(self, init_meme, mock_keyring, isolated_vault):
        """Saving a vault memory should create an .enc file."""
        from meme.vault import save_vault_memory

        meta = {
            "id": "mem_vault_1",
            "type": "knowledge",
            "tags": ["secret"],
        }
        body = "API token: sk-live-abc123"

        enc_path = save_vault_memory("mem_vault_1", meta, body)

        assert enc_path.exists()
        assert enc_path.suffix == ".enc"

    def test_save_vault_memory_creates_index_entry(self, init_meme, mock_keyring, isolated_vault):
        """Saving a vault memory should update _vault.json index."""
        from meme.vault import save_vault_memory

        meta = {
            "id": "mem_vault_2",
            "type": "knowledge",
            "tags": ["secret"],
        }
        body = "my API token: sk-live-xyz789"

        save_vault_memory("mem_vault_2", meta, body)

        vault_index_path = isolated_vault / "_vault.json"
        assert vault_index_path.exists()
        index = json.loads(vault_index_path.read_text())
        assert "mem_vault_2" in index
        assert index["mem_vault_2"]["encrypted"] is True
        assert "API token" in index["mem_vault_2"]["summary"]

    def test_load_vault_memory_roundtrip(self, init_meme, mock_keyring, isolated_vault):
        """Save then load vault memory should return original content."""
        from meme.vault import save_vault_memory, load_vault_memory
        from cryptography.fernet import Fernet

        # Use a fixed key for consistent encrypt/decrypt
        fixed_key = Fernet.generate_key()
        mock_keyring.get_password.return_value = fixed_key.decode()

        meta = {
            "id": "mem_vault_3",
            "type": "knowledge",
            "importance": 0.8,
            "tags": ["api"],
        }
        body = "Secret value: shh-dont-tell"

        save_vault_memory("mem_vault_3", meta, body)
        result = load_vault_memory("mem_vault_3")

        assert result is not None
        loaded_meta, loaded_body = result
        assert loaded_body == "shh-dont-tell"
        assert loaded_meta.get("type") == "knowledge"

    def test_load_vault_memory_missing(self, init_meme):
        """Loading a non-existent vault memory should return None."""
        from meme.vault import load_vault_memory

        result = load_vault_memory("mem_nonexistent_vault")
        assert result is None

    def test_save_vault_memory_colon_separator(self, init_meme, mock_keyring, isolated_vault):
        """Vault memory with colon separator should extract description correctly."""
        from meme.vault import save_vault_memory

        meta = {"id": "mem_vault_colon", "type": "knowledge", "tags": []}
        body = "API key: sk-live-abc123"

        save_vault_memory("mem_vault_colon", meta, body)

        vault_index_path = isolated_vault / "_vault.json"
        index = json.loads(vault_index_path.read_text())
        assert "API key" in index["mem_vault_colon"]["summary"]

    def test_save_vault_memory_no_separator(self, init_meme, mock_keyring, isolated_vault):
        """Vault memory without separator should use type as description."""
        from meme.vault import save_vault_memory

        meta = {"id": "mem_vault_no_sep", "type": "reference", "tags": []}
        body = "just-a-plain-secret-without-description"

        save_vault_memory("mem_vault_no_sep", meta, body)

        vault_index_path = isolated_vault / "_vault.json"
        index = json.loads(vault_index_path.read_text())
        assert index["mem_vault_no_sep"]["summary"] == "REFERENCE"


# ========================================
# Memory string serialization tests
# ========================================

class TestMemoryStringSerialization:
    def test_save_memory_to_string(self, init_meme):
        """save_memory_to_string should produce valid frontmatter markdown."""
        from meme.vault import save_memory_to_string

        meta = {"id": "mem_test", "type": "feedback", "importance": 0.8}
        body = "test content"

        result = save_memory_to_string(meta, body)
        assert result.startswith("---\n")
        assert "id: mem_test" in result
        assert "type: feedback" in result
        assert body in result

    def test_parse_memory_string(self, init_meme):
        """parse_memory_string should extract frontmatter and body."""
        from meme.vault import parse_memory_string

        text = "---\nid: mem_test\ntype: feedback\n---\n\nbody content here"
        meta, body = parse_memory_string(text)

        assert meta["id"] == "mem_test"
        assert meta["type"] == "feedback"
        assert body == "body content here"

    def test_parse_memory_string_no_frontmatter(self, init_meme):
        """parse_memory_string without frontmatter should return empty meta."""
        from meme.vault import parse_memory_string

        text = "just plain content"
        meta, body = parse_memory_string(text)

        assert meta == {}
        assert body == "just plain content"

    def test_parse_memory_string_unicode(self, init_meme):
        """parse_memory_string should handle unicode content."""
        from meme.vault import parse_memory_string

        text = "---\nid: mem_test\n---\n\nchinese content"
        meta, body = parse_memory_string(text)

        assert body == "chinese content"

    def test_roundtrip_serialization(self, init_meme):
        """serialize then parse should return equivalent data."""
        from meme.vault import save_memory_to_string, parse_memory_string

        meta = {"id": "mem_rt", "type": "knowledge", "tags": ["a", "b"]}
        body = "roundtrip content"

        serialized = save_memory_to_string(meta, body)
        parsed_meta, parsed_body = parse_memory_string(serialized)

        assert parsed_meta["id"] == meta["id"]
        assert parsed_meta["type"] == meta["type"]
        assert parsed_body == body


# ========================================
# Auth / Run command tests (vault-specific)
# ========================================

class TestAuthRun:
    def test_run_missing_command_fails(self, init_meme, capsys):
        """Run without command should fail."""
        from meme.commands.system import cmd_run

        args = type("Args", (), {"mem_id": "mem_vault", "var": None, "cmd": []})()
        with pytest.raises(SystemExit) as exc_info:
            cmd_run(args)
        assert exc_info.value.code == 1
