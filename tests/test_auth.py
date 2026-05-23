import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from notesync import safestorage
from notesync.auth import GranolaAccount, GranolaAuth, is_token_expired, jwt_expires_at


def _make_jwt(payload: dict) -> str:
    """Build a syntactically valid JWT (header.payload.sig) with the given
    payload. Signature is empty — we don't verify, we only decode."""
    def b64(obj):
        raw = json.dumps(obj).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    header = b64({"alg": "none", "typ": "JWT"})
    body = b64(payload)
    return f"{header}.{body}."


@pytest.fixture
def auth_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Redirect both candidate config paths into tmp_path so tests never read
    the real Granola files. Returns (supabase_path, stored_accounts_path).
    """
    supabase = tmp_path / "supabase.json"
    stored_accounts = tmp_path / "stored-accounts.json"

    monkeypatch.setattr(
        GranolaAuth, "get_supabase_config_path", staticmethod(lambda: str(supabase))
    )
    monkeypatch.setattr(
        GranolaAuth, "_get_stored_accounts_path", staticmethod(lambda: str(stored_accounts))
    )

    return supabase, stored_accounts


def _account_entry(*, user_id: str, email: str, access_token: str) -> dict:
    """One `accounts[]` entry as Granola writes it: `tokens` is stringified JSON."""
    tokens = json.dumps({"access_token": access_token, "refresh_token": "rt"})
    return {"userId": user_id, "email": email, "tokens": tokens}


def _stored_accounts_payload(access_token: str) -> dict:
    """Build a `stored-accounts.json` body with the double-stringified shape."""
    return {
        "accounts": json.dumps(
            [_account_entry(user_id="u", email="e@example.com", access_token=access_token)]
        )
    }


def _supabase_with_user_info(*, user_id: str, email: str, access_token: str) -> dict:
    """Build a `supabase.json` body with workos tokens AND a parseable user_info."""
    return {
        "workos_tokens": {"access_token": access_token},
        "user_info": json.dumps({"id": user_id, "email": email}),
    }


def test_workos_dict_form(auth_paths):
    supabase, _ = auth_paths
    supabase.write_text(json.dumps({"workos_tokens": {"access_token": "workos-dict"}}))

    assert GranolaAuth.get_access_token() == "workos-dict"


def test_workos_stringified_form(auth_paths):
    supabase, _ = auth_paths
    supabase.write_text(
        json.dumps({"workos_tokens": json.dumps({"access_token": "workos-str"})})
    )

    assert GranolaAuth.get_access_token() == "workos-str"


def test_cognito_fallback(auth_paths):
    supabase, _ = auth_paths
    supabase.write_text(json.dumps({"cognito_tokens": {"access_token": "cognito-tok"}}))

    assert GranolaAuth.get_access_token() == "cognito-tok"


def test_stored_accounts_only(auth_paths):
    _, stored_accounts = auth_paths
    stored_accounts.write_text(json.dumps(_stored_accounts_payload("new-fmt-tok")))

    assert GranolaAuth.get_access_token() == "new-fmt-tok"


def test_corrupted_stored_accounts_falls_through_to_supabase(auth_paths):
    """
    `stored-accounts.json` is tried first; if it's corrupted/unreadable we
    should still recover via legacy `supabase.json`.
    """
    supabase, stored_accounts = auth_paths
    stored_accounts.write_text("{not valid json")
    supabase.write_text(json.dumps({"workos_tokens": {"access_token": "legacy-fallback"}}))

    assert GranolaAuth.get_access_token() == "legacy-fallback"


def test_supabase_only_still_works(auth_paths):
    """
    Users on older Granola builds (no `stored-accounts.json` yet) must keep
    working via the legacy `supabase.json` path.
    """
    supabase, _ = auth_paths
    supabase.write_text(json.dumps({"workos_tokens": {"access_token": "legacy-only"}}))

    assert GranolaAuth.get_access_token() == "legacy-only"


def test_stored_accounts_wins_when_both_present(auth_paths):
    """
    Granola app updates leave a stale `supabase.json` behind, so when both
    files exist on disk the newer `stored-accounts.json` must be preferred —
    its tokens are the ones Granola is still refreshing.
    """
    supabase, stored_accounts = auth_paths
    supabase.write_text(json.dumps({"workos_tokens": {"access_token": "stale-legacy"}}))
    stored_accounts.write_text(json.dumps(_stored_accounts_payload("fresh-new-fmt")))

    assert GranolaAuth.get_access_token() == "fresh-new-fmt"


def test_neither_file_exists_raises_with_both_paths(auth_paths):
    supabase, stored_accounts = auth_paths

    with pytest.raises(FileNotFoundError) as excinfo:
        GranolaAuth.get_access_token()

    message = str(excinfo.value)
    assert str(supabase) in message
    assert str(stored_accounts) in message


def test_empty_accounts_array_raises_value_error(auth_paths):
    _, stored_accounts = auth_paths
    stored_accounts.write_text(json.dumps({"accounts": json.dumps([])}))

    with pytest.raises(ValueError):
        GranolaAuth.get_access_token()


def test_non_dict_json_top_level_falls_through(auth_paths):
    """
    If `stored-accounts.json` contains a top-level JSON array or string
    (corruption / unexpected format), extractors must not crash on
    `.get(...)`; we should fall through to the legacy file instead.
    """
    supabase, stored_accounts = auth_paths
    stored_accounts.write_text(json.dumps(["not", "a", "dict"]))
    supabase.write_text(json.dumps({"workos_tokens": {"access_token": "recovered"}}))

    assert GranolaAuth.get_access_token() == "recovered"


# ---------------------------------------------------------------------------
# Multi-account: list_accounts()
# ---------------------------------------------------------------------------


def test_list_accounts_returns_all_stored_accounts_in_order(auth_paths):
    _, stored_accounts = auth_paths
    stored_accounts.write_text(
        json.dumps(
            {
                "accounts": json.dumps(
                    [
                        _account_entry(user_id="u1", email="a@x.com", access_token="tok-a"),
                        _account_entry(user_id="u2", email="b@x.com", access_token="tok-b"),
                    ]
                )
            }
        )
    )

    accounts = GranolaAuth.list_accounts()
    assert [(a.email, a.access_token, a.source) for a in accounts] == [
        ("a@x.com", "tok-a", "stored-accounts"),
        ("b@x.com", "tok-b", "stored-accounts"),
    ]


def test_list_accounts_skips_entries_missing_email_or_tokens(auth_paths):
    _, stored_accounts = auth_paths
    stored_accounts.write_text(
        json.dumps(
            {
                "accounts": json.dumps(
                    [
                        {"userId": "u1", "email": "ok@x.com", "tokens": json.dumps({"access_token": "t"})},
                        {"userId": "u2", "tokens": json.dumps({"access_token": "t"})},  # no email
                        {"userId": "u3", "email": "no-tokens@x.com"},  # no tokens
                        {"userId": "u4", "email": "empty@x.com", "tokens": json.dumps({})},  # tokens without access_token
                    ]
                )
            }
        )
    )

    accounts = GranolaAuth.list_accounts()
    assert [a.email for a in accounts] == ["ok@x.com"]


def test_list_accounts_merges_legacy_supabase_when_distinct(auth_paths):
    supabase, stored_accounts = auth_paths
    stored_accounts.write_text(
        json.dumps(
            {
                "accounts": json.dumps(
                    [_account_entry(user_id="u-stored", email="stored@x.com", access_token="tok-stored")]
                )
            }
        )
    )
    supabase.write_text(
        json.dumps(_supabase_with_user_info(user_id="u-legacy", email="legacy@x.com", access_token="tok-legacy"))
    )

    accounts = GranolaAuth.list_accounts()
    assert len(accounts) == 2
    assert accounts[0].email == "stored@x.com"
    assert accounts[0].source == "stored-accounts"
    assert accounts[1].email == "legacy@x.com"
    assert accounts[1].source == "supabase"


def test_list_accounts_deduplicates_legacy_by_user_id(auth_paths):
    """
    If supabase.json's user_id matches an account already in stored-accounts.json,
    the legacy entry must NOT be appended — Granola is in the middle of migrating
    that identity and the supabase.json token is the stale one.
    """
    supabase, stored_accounts = auth_paths
    stored_accounts.write_text(
        json.dumps(
            {
                "accounts": json.dumps(
                    [_account_entry(user_id="shared-id", email="shared@x.com", access_token="tok-fresh")]
                )
            }
        )
    )
    supabase.write_text(
        json.dumps(_supabase_with_user_info(user_id="shared-id", email="shared@x.com", access_token="tok-stale"))
    )

    accounts = GranolaAuth.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].access_token == "tok-fresh"


def test_list_accounts_deduplicates_legacy_by_email(auth_paths):
    """Same identity, different user_id (unlikely but defensive): dedupe on email."""
    supabase, stored_accounts = auth_paths
    stored_accounts.write_text(
        json.dumps(
            {
                "accounts": json.dumps(
                    [_account_entry(user_id="u-stored", email="dup@x.com", access_token="tok-fresh")]
                )
            }
        )
    )
    supabase.write_text(
        json.dumps(_supabase_with_user_info(user_id="u-other", email="dup@x.com", access_token="tok-stale"))
    )

    accounts = GranolaAuth.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].email == "dup@x.com"


def test_list_accounts_prefers_fresh_legacy_when_stored_token_expired(auth_paths):
    """
    Recent Granola builds refresh only the active account's token in
    `supabase.json` while leaving `stored-accounts.json` stale. When the same
    identity is expired in stored-accounts but fresh in supabase, list_accounts
    must swap in the fresh legacy token (rather than keep the dead stored one).
    """
    supabase, stored_accounts = auth_paths
    expired = _make_jwt({"exp": 100})  # 1970 -> long expired
    fresh = _make_jwt({"exp": 9_999_999_999})  # year 2286 -> valid
    stored_accounts.write_text(
        json.dumps(
            {
                "accounts": json.dumps(
                    [_account_entry(user_id="shared-id", email="shared@x.com", access_token=expired)]
                )
            }
        )
    )
    supabase.write_text(
        json.dumps(_supabase_with_user_info(user_id="shared-id", email="shared@x.com", access_token=fresh))
    )

    accounts = GranolaAuth.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].email == "shared@x.com"
    assert accounts[0].access_token == fresh
    assert accounts[0].source == "supabase"


def test_list_accounts_keeps_stored_when_both_tokens_expired(auth_paths):
    """If both copies of an identity are expired, don't churn the entry: keep
    the stored-accounts one (a swap would buy nothing)."""
    supabase, stored_accounts = auth_paths
    stored_expired = _make_jwt({"exp": 100})
    legacy_expired = _make_jwt({"exp": 200})
    stored_accounts.write_text(
        json.dumps(
            {
                "accounts": json.dumps(
                    [_account_entry(user_id="shared-id", email="shared@x.com", access_token=stored_expired)]
                )
            }
        )
    )
    supabase.write_text(
        json.dumps(_supabase_with_user_info(user_id="shared-id", email="shared@x.com", access_token=legacy_expired))
    )

    accounts = GranolaAuth.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].access_token == stored_expired
    assert accounts[0].source == "stored-accounts"


def test_list_accounts_no_duplicate_when_multiple_stored_match_legacy(auth_paths):
    """If two stored entries both match the legacy identity (one by user_id,
    one by email), the legacy entry must not be appended as a third copy."""
    supabase, stored_accounts = auth_paths
    stored_accounts.write_text(
        json.dumps(
            {
                "accounts": json.dumps(
                    [
                        _account_entry(user_id="shared-id", email="other@x.com", access_token="tok-1"),
                        _account_entry(user_id="different-id", email="dup@x.com", access_token="tok-2"),
                    ]
                )
            }
        )
    )
    supabase.write_text(
        json.dumps(_supabase_with_user_info(user_id="shared-id", email="dup@x.com", access_token="tok-legacy"))
    )

    accounts = GranolaAuth.list_accounts()
    # Legacy matches entry 0 by user_id and entry 1 by email -> not appended.
    assert len(accounts) == 2
    assert all(a.source == "stored-accounts" for a in accounts)


def test_list_accounts_raises_file_not_found_when_no_files(auth_paths):
    with pytest.raises(FileNotFoundError):
        GranolaAuth.list_accounts()


def test_list_accounts_raises_value_error_when_files_have_no_accounts(auth_paths):
    _, stored_accounts = auth_paths
    stored_accounts.write_text(json.dumps({"accounts": json.dumps([])}))

    with pytest.raises(ValueError):
        GranolaAuth.list_accounts()


def test_list_accounts_falls_back_to_supabase_when_stored_corrupted(auth_paths):
    supabase, stored_accounts = auth_paths
    stored_accounts.write_text("{not json")
    supabase.write_text(
        json.dumps(_supabase_with_user_info(user_id="u", email="only@x.com", access_token="tok-only"))
    )

    accounts = GranolaAuth.list_accounts()
    assert [(a.email, a.access_token, a.source) for a in accounts] == [
        ("only@x.com", "tok-only", "supabase")
    ]


def test_list_accounts_returns_dataclass_instances(auth_paths):
    _, stored_accounts = auth_paths
    stored_accounts.write_text(json.dumps(_stored_accounts_payload("tok")))

    accounts = GranolaAuth.list_accounts()
    assert isinstance(accounts[0], GranolaAccount)
    assert accounts[0].user_id == "u"


# ---------------------------------------------------------------------------
# JWT expiry inspection
# ---------------------------------------------------------------------------


def test_jwt_expires_at_returns_exp_claim():
    token = _make_jwt({"exp": 1_700_000_000, "iat": 1_699_000_000})
    assert jwt_expires_at(token) == 1_700_000_000


def test_jwt_expires_at_returns_none_for_garbage_token():
    assert jwt_expires_at("not-a-jwt") is None
    assert jwt_expires_at("") is None
    assert jwt_expires_at("a.b") is None  # missing third segment
    # Valid 3-segment shape but payload isn't base64-decodable JSON.
    assert jwt_expires_at("aaa.@@@.bbb") is None


def test_jwt_expires_at_returns_none_when_exp_absent_or_non_numeric():
    no_exp = _make_jwt({"iat": 1_700_000_000})
    assert jwt_expires_at(no_exp) is None
    bad_exp = _make_jwt({"exp": "not-a-number"})
    assert jwt_expires_at(bad_exp) is None


def test_is_token_expired_when_exp_in_past():
    token = _make_jwt({"exp": 100})
    assert is_token_expired(token, now=1_000_000) is True


def test_is_token_expired_false_when_exp_in_future():
    token = _make_jwt({"exp": 2_000_000})
    assert is_token_expired(token, now=1_000_000) is False


def test_is_token_expired_buffer_skips_near_expiry_tokens():
    """A token expiring within the buffer is treated as already expired so we
    don't race the API and 401 mid-request."""
    token = _make_jwt({"exp": 1_000_030})  # 30s away
    assert is_token_expired(token, now=1_000_000, buffer_seconds=60) is True
    assert is_token_expired(token, now=1_000_000, buffer_seconds=10) is False


def test_is_token_expired_treats_undecodable_as_fresh():
    """If we can't read `exp` we'd rather attempt the call and surface a real
    401 than silently skip the account because of a decoder edge case."""
    assert is_token_expired("garbage", now=1_000_000) is False
    assert is_token_expired("", now=1_000_000) is False


# ---------------------------------------------------------------------------
# Encrypted store (.enc) reading
# ---------------------------------------------------------------------------


@pytest.fixture
def enc_env(monkeypatch):
    """Fixed DEK via override + force the platform check on, so .enc reading
    works on any CI host. Returns the 32-byte DEK for sealing fixtures."""
    dek = bytes(range(32))
    monkeypatch.setenv("NOTESYNC_GRANOLA_DEK", base64.b64encode(dek).decode())
    monkeypatch.setattr(safestorage, "is_supported", lambda: True)
    safestorage.reset_cache()
    yield dek
    safestorage.reset_cache()


def _seal(obj: dict, dek: bytes) -> bytes:
    nonce = b"\x00" * 12
    return nonce + AESGCM(dek).encrypt(nonce, json.dumps(obj).encode(), None)


def test_list_accounts_reads_encrypted_stored_accounts(auth_paths, enc_env):
    """When only the encrypted stored-accounts file exists, decrypt and use it."""
    _, stored_accounts = auth_paths
    enc = Path(str(stored_accounts) + ".enc")
    enc.write_bytes(
        _seal(
            {"accounts": json.dumps(
                [_account_entry(user_id="u1", email="a@x.com", access_token="tok-a")]
            )},
            enc_env,
        )
    )

    accounts = GranolaAuth.list_accounts()
    assert [(a.email, a.access_token, a.source) for a in accounts] == [
        ("a@x.com", "tok-a", "stored-accounts")
    ]


def test_encrypted_stored_accounts_wins_over_stale_plaintext(auth_paths, enc_env):
    """Both files present: the encrypted copy is authoritative; plaintext is
    the frozen orphan modern Granola leaves behind."""
    _, stored_accounts = auth_paths
    stored_accounts.write_text(
        json.dumps(
            {"accounts": json.dumps(
                [_account_entry(user_id="u1", email="a@x.com", access_token="STALE-plaintext")]
            )}
        )
    )
    Path(str(stored_accounts) + ".enc").write_bytes(
        _seal(
            {"accounts": json.dumps(
                [_account_entry(user_id="u1", email="a@x.com", access_token="FRESH-encrypted")]
            )},
            enc_env,
        )
    )

    accounts = GranolaAuth.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].access_token == "FRESH-encrypted"


def test_encrypted_decrypt_failure_falls_back_to_plaintext(auth_paths, enc_env, capsys):
    """A corrupt/unreadable .enc should not wipe out a usable plaintext; the
    cause is reported on stderr rather than silently swallowed."""
    _, stored_accounts = auth_paths
    stored_accounts.write_text(
        json.dumps(
            {"accounts": json.dumps(
                [_account_entry(user_id="u1", email="a@x.com", access_token="plaintext-fallback")]
            )}
        )
    )
    Path(str(stored_accounts) + ".enc").write_bytes(b"not-a-valid-gcm-envelope")

    accounts = GranolaAuth.list_accounts()
    assert accounts[0].access_token == "plaintext-fallback"
    assert "could not read" in capsys.readouterr().err


def test_get_access_token_reads_encrypted_supabase(auth_paths, enc_env):
    """Single-account API also honors the encrypted store."""
    supabase, _ = auth_paths
    Path(str(supabase) + ".enc").write_bytes(
        _seal({"workos_tokens": {"access_token": "enc-workos"}}, enc_env)
    )
    assert GranolaAuth.get_access_token() == "enc-workos"
