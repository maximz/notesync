"""Tests for the encrypted-store (.enc) reader."""

import base64
import json
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from notesync import safestorage


def _seal(plaintext: bytes, dek: bytes) -> bytes:
    """Build a `nonce(12) || ciphertext || tag(16)` GCM blob like Granola does."""
    nonce = b"\x00" * 12  # deterministic nonce is fine for a round-trip test
    return nonce + AESGCM(dek).encrypt(nonce, plaintext, None)


@pytest.fixture
def dek_override(monkeypatch):
    """Provide a fixed 32-byte DEK via the override env var; reset cache around."""
    dek = bytes(range(32))
    monkeypatch.setenv("NOTESYNC_GRANOLA_DEK", base64.b64encode(dek).decode())
    safestorage.reset_cache()
    yield dek
    safestorage.reset_cache()


def test_get_dek_uses_override(dek_override):
    assert safestorage.get_dek() == dek_override


def test_get_dek_rejects_bad_override(monkeypatch):
    monkeypatch.setenv("NOTESYNC_GRANOLA_DEK", base64.b64encode(b"too-short").decode())
    with pytest.raises(safestorage.SafeStorageError):
        safestorage.get_dek()


def test_decrypt_roundtrip(dek_override):
    blob = _seal(b'{"hello": "world"}', dek_override)
    assert safestorage.decrypt(blob) == b'{"hello": "world"}'


def test_decrypt_rejects_short_blob(dek_override):
    with pytest.raises(safestorage.SafeStorageError):
        safestorage.decrypt(b"tiny")


def test_decrypt_rejects_tampered_blob(dek_override):
    blob = bytearray(_seal(b'{"a": 1}', dek_override))
    blob[-1] ^= 0xFF  # corrupt the tag
    with pytest.raises(safestorage.SafeStorageError):
        safestorage.decrypt(bytes(blob))


def test_decrypt_wrong_key_fails(dek_override):
    blob = _seal(b'{"a": 1}', dek_override)
    with pytest.raises(safestorage.SafeStorageError):
        safestorage.decrypt(blob, dek=bytes(reversed(dek_override)))


def test_load_encrypted_json_roundtrip(tmp_path, dek_override):
    payload = {"accounts": "[]", "x": 1}
    enc = tmp_path / "stored-accounts.json.enc"
    enc.write_bytes(_seal(json.dumps(payload).encode(), dek_override))
    assert safestorage.load_encrypted_json(enc) == payload


def test_load_encrypted_json_non_object(tmp_path, dek_override):
    enc = tmp_path / "x.json.enc"
    enc.write_bytes(_seal(b"[1, 2, 3]", dek_override))
    with pytest.raises(safestorage.SafeStorageError):
        safestorage.load_encrypted_json(enc)
