"""
Decrypt Granola's encrypted local-storage files.

Recent Granola desktop builds (macOS, ~2026) stopped writing plaintext config
and now keep tokens and cache in AES-256-GCM `.enc` files under a two-tier
scheme:

  1. `storage.dek` holds a 32-byte Data Encryption Key (DEK), wrapped with
     Electron's safeStorage "v10" envelope (Chromium OSCrypt on macOS):
     AES-128-CBC under a key derived by PBKDF2-HMAC-SHA1 (salt "saltysalt",
     1003 iterations, 16-byte key) from the macOS Keychain secret, with a
     16-byte ASCII-space IV and PKCS7 padding. The decrypted plaintext is a
     base64 string whose decoded bytes are the 32-byte DEK.
  2. `<name>.json.enc` files are AES-256-GCM with the DEK as the key and the
     envelope `nonce(12) || ciphertext || tag(16)` (no associated data).

Reading is entirely passive: it never writes, never refreshes tokens, and
never touches Granola's auth flow, so it cannot disturb the desktop app. macOS
only -- callers fall back to plaintext config on other platforms or when the
encrypted store / Keychain entry is unavailable.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import subprocess
import threading
from pathlib import Path
from typing import Optional, Union


class SafeStorageError(Exception):
    """Raised when the encrypted store cannot be read or decrypted."""


# Layer-1 (Electron safeStorage "v10" / Chromium OSCrypt) constants.
_V10_PREFIX = b"v10"
_PBKDF2_SALT = b"saltysalt"
_PBKDF2_ITERS = 1003
_PBKDF2_KEYLEN = 16
_CBC_IV = b"\x20" * 16  # 16 ASCII spaces

# Layer-2 (file payload) constants.
_DEK_LEN = 32
_GCM_NONCE = 12
_GCM_TAG = 16

_KEYCHAIN_SERVICE = "Granola Safe Storage"
_KEYCHAIN_ACCOUNT = "Granola Key"

# The DEK is stable for the life of a Granola login, so unwrap it at most once
# per process -- the Keychain lookup is the only step that can prompt the user.
_dek_lock = threading.Lock()
_dek_cache: Optional[bytes] = None


def is_supported() -> bool:
    """True on platforms where the encrypted-store unwrap is implemented."""
    return platform.system() == "Darwin"


def _granola_dir() -> Path:
    env = os.environ.get("GRANOLA_SUPPORT_DIR")
    if env:
        return Path(env)
    return Path.home() / "Library" / "Application Support" / "Granola"


def _storage_dek_path() -> Path:
    return _granola_dir() / "storage.dek"


def _fetch_keychain_secret() -> str:
    """
    Return the base64 Keychain secret. The base64 *string* (not its decoded
    bytes) is the PBKDF2 password, matching Chromium OSCrypt on macOS.
    """
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                _KEYCHAIN_SERVICE,
                "-a",
                _KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
            # Generous so the one-time "Always Allow" Keychain dialog has time
            # to be approved; once granted, `security` returns near-instantly.
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise SafeStorageError(f"could not run /usr/bin/security: {e}") from e

    if result.returncode != 0:
        msg = (result.stderr or "").strip()
        raise SafeStorageError(
            f"Keychain entry {_KEYCHAIN_SERVICE!r}/{_KEYCHAIN_ACCOUNT!r} unavailable "
            f"({msg or 'access denied or not found'}). "
            "Sign in to Granola, then approve the Keychain prompt (Always Allow)."
        )

    secret = result.stdout.strip()
    if not secret:
        raise SafeStorageError("Keychain returned an empty secret")
    return secret


def _unwrap_dek() -> bytes:
    """Derive the KEK from the Keychain and AES-128-CBC decrypt storage.dek."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    secret = _fetch_keychain_secret()

    dek_path = _storage_dek_path()
    try:
        blob = dek_path.read_bytes()
    except FileNotFoundError as e:
        raise SafeStorageError(
            f"storage.dek not found at {dek_path} "
            "(Granola not installed, or a pre-encryption build)"
        ) from e
    except OSError as e:
        raise SafeStorageError(f"could not read {dek_path}: {e}") from e

    if not blob.startswith(_V10_PREFIX):
        raise SafeStorageError("storage.dek is not in the expected v10 format")
    body = blob[len(_V10_PREFIX):]
    if not body or len(body) % 16 != 0:
        raise SafeStorageError("storage.dek body is not block-aligned")

    kek = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=_PBKDF2_KEYLEN,
        salt=_PBKDF2_SALT,
        iterations=_PBKDF2_ITERS,
    ).derive(secret.encode())

    decryptor = Cipher(algorithms.AES(kek), modes.CBC(_CBC_IV)).decryptor()
    padded = decryptor.update(body) + decryptor.finalize()

    pad = padded[-1] if padded else 0
    if pad < 1 or pad > 16 or padded[-pad:] != bytes([pad]) * pad:
        raise SafeStorageError("storage.dek PKCS7 padding is invalid (wrong Keychain key?)")
    plain = padded[:-pad]

    try:
        dek = base64.b64decode(plain, validate=True)
    except Exception as e:
        raise SafeStorageError(f"storage.dek plaintext is not valid base64: {e}") from e
    if len(dek) != _DEK_LEN:
        raise SafeStorageError(f"DEK length {len(dek)}, expected {_DEK_LEN}")
    return dek


def get_dek() -> bytes:
    """
    Return the 32-byte DEK, cached for the process.

    `NOTESYNC_GRANOLA_DEK` (base64 of 32 bytes) overrides the Keychain unwrap;
    it exists for tests/CI that cannot prompt the Keychain.
    """
    override = os.environ.get("NOTESYNC_GRANOLA_DEK")
    if override:
        try:
            dek = base64.b64decode(override, validate=True)
        except Exception as e:
            raise SafeStorageError(f"NOTESYNC_GRANOLA_DEK is not valid base64: {e}") from e
        if len(dek) != _DEK_LEN:
            raise SafeStorageError(
                f"NOTESYNC_GRANOLA_DEK decodes to {len(dek)} bytes, expected {_DEK_LEN}"
            )
        return dek

    global _dek_cache
    with _dek_lock:
        if _dek_cache is None:
            _dek_cache = _unwrap_dek()
        return _dek_cache


def reset_cache() -> None:
    """Drop the cached DEK so the next call re-reads the Keychain."""
    global _dek_cache
    with _dek_lock:
        _dek_cache = None


def decrypt(blob: bytes, dek: Optional[bytes] = None) -> bytes:
    """AES-256-GCM decrypt a `nonce(12) || ciphertext || tag(16)` blob."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(blob) < _GCM_NONCE + _GCM_TAG + 1:
        raise SafeStorageError("ciphertext too short to be a valid GCM envelope")
    if dek is None:
        dek = get_dek()
    nonce, body = blob[:_GCM_NONCE], blob[_GCM_NONCE:]
    try:
        return AESGCM(dek).decrypt(nonce, body, None)
    except Exception as e:
        raise SafeStorageError(f"GCM authentication failed: {e}") from e


def load_encrypted_json(enc_path: Union[str, "os.PathLike[str]"]) -> dict:
    """Decrypt and JSON-parse a Granola `.enc` file into its top-level dict."""
    path = Path(enc_path)
    try:
        blob = path.read_bytes()
    except OSError as e:
        raise SafeStorageError(f"could not read {path}: {e}") from e
    try:
        data = json.loads(decrypt(blob))
    except json.JSONDecodeError as e:
        raise SafeStorageError(f"{path} did not decrypt to valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise SafeStorageError(f"{path} did not decrypt to a JSON object")
    return data
