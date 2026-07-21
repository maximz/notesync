"""Tests for notesync.tokens — token store and refresh logic."""
import base64
import json
import os
import stat
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from notesync.auth import GranolaAccount, GranolaAuth
from notesync.tokens import (
    TokenPersistError,
    TokenRefreshDead,
    TokenRefreshError,
    TokenRefreshTransient,
    _account_subdir,
    _get_token_store_dir,
    _persist,
    _read_token_store,
    _write_token_store,
    get_access_token_for,
    refresh_access_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jwt(payload: dict) -> str:
    """Build a syntactically valid JWT with the given payload (signature empty)."""
    def b64(obj):
        raw = json.dumps(obj).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    header = b64({"alg": "none", "typ": "JWT"})
    body = b64(payload)
    return f"{header}.{body}."


def _fresh_jwt() -> str:
    return _make_jwt({"exp": int(time.time()) + 3600})


def _stale_jwt() -> str:
    return _make_jwt({"exp": 100})


@pytest.fixture
def token_dir(tmp_path, monkeypatch):
    """Set NOTESYNC_TOKEN_DIR to a fresh temp subdir for each test."""
    d = tmp_path / "tokens"
    monkeypatch.setenv("NOTESYNC_TOKEN_DIR", str(d))
    return d


@pytest.fixture
def auth_paths(tmp_path, monkeypatch):
    """Redirect Granola config paths into tmp_path so tests never read real files."""
    supabase = tmp_path / "supabase.json"
    stored_accounts = tmp_path / "stored-accounts.json"
    monkeypatch.setattr(GranolaAuth, "get_supabase_config_path", staticmethod(lambda: str(supabase)))
    monkeypatch.setattr(GranolaAuth, "_get_stored_accounts_path", staticmethod(lambda: str(stored_accounts)))
    return supabase, stored_accounts


def _make_account(email="test@example.com", access_token="at", refresh_token="rt") -> GranolaAccount:
    return GranolaAccount(email=email, access_token=access_token, refresh_token=refresh_token)


# ---------------------------------------------------------------------------
# refresh_token extraction from auth.py
# ---------------------------------------------------------------------------


def _account_entry(*, user_id: str, email: str, access_token: str, refresh_token: str = "rt") -> dict:
    tokens = json.dumps({"access_token": access_token, "refresh_token": refresh_token})
    return {"userId": user_id, "email": email, "tokens": tokens}


def test_stored_accounts_refresh_token_extracted(auth_paths):
    _, stored_accounts = auth_paths
    stored_accounts.write_text(
        json.dumps({
            "accounts": json.dumps([
                _account_entry(user_id="u", email="e@example.com", access_token="at", refresh_token="my-rt")
            ])
        })
    )
    accounts = GranolaAuth.list_accounts()
    assert accounts[0].refresh_token == "my-rt"


def test_supabase_account_refresh_token_from_workos(auth_paths):
    supabase, _ = auth_paths
    supabase.write_text(json.dumps({
        "workos_tokens": {"access_token": "at-workos", "refresh_token": "rt-workos"},
        "user_info": json.dumps({"id": "u1", "email": "a@x.com"}),
    }))
    accounts = GranolaAuth.list_accounts()
    assert accounts[0].refresh_token == "rt-workos"


def test_supabase_account_refresh_token_from_cognito(auth_paths):
    supabase, _ = auth_paths
    supabase.write_text(json.dumps({
        "cognito_tokens": {"access_token": "at-cognito", "refresh_token": "rt-cognito"},
        "user_info": json.dumps({"id": "u1", "email": "b@x.com"}),
    }))
    accounts = GranolaAuth.list_accounts()
    assert accounts[0].refresh_token == "rt-cognito"


# ---------------------------------------------------------------------------
# refresh_access_token function
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, data=None, headers=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if data is not None:
        resp.json.return_value = data
        resp.text = json.dumps(data)
    else:
        resp.json.side_effect = ValueError("no body")
        resp.text = ""
    return resp


def test_refresh_access_token_success():
    mock_resp = _mock_response(200, {"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600})
    with patch("requests.post", return_value=mock_resp):
        result = refresh_access_token("old-rt")
    assert result["access_token"] == "new-at"
    assert result["refresh_token"] == "new-rt"
    assert result["expires_in"] == 3600


def test_refresh_access_token_missing_access_token_raises():
    mock_resp = _mock_response(200, {"access_token": "", "refresh_token": "rt"})
    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(TokenRefreshDead, match="access_token is missing or empty"):
            refresh_access_token("old-rt")


def test_refresh_access_token_401_raises_dead():
    mock_resp = _mock_response(401, {"error": "invalid_grant"})
    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(TokenRefreshDead, match="401"):
            refresh_access_token("bad-rt")


def test_refresh_access_token_403_raises_dead():
    mock_resp = _mock_response(403, {"error": "forbidden"})
    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(TokenRefreshDead, match="403"):
            refresh_access_token("bad-rt")


def test_refresh_access_token_invalid_grant_in_non_401_body_raises_dead():
    """A body indicating invalid_grant is dead even if the status isn't 401/403."""
    mock_resp = _mock_response(400, {"error": "invalid_grant"})
    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(TokenRefreshDead, match="invalid_grant"):
            refresh_access_token("bad-rt")


def test_refresh_access_token_5xx_raises_transient():
    """An unclassified server error is retryable, not a dead token."""
    mock_resp = _mock_response(503, {"error": "unavailable"})
    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(TokenRefreshTransient, match="503"):
            refresh_access_token("rt")


def test_refresh_access_token_network_error_raises_transient():
    with patch("requests.post", side_effect=requests.ConnectionError("boom")):
        with pytest.raises(TokenRefreshTransient, match="network error"):
            refresh_access_token("rt")


def test_refresh_access_token_429_exhausted_raises_transient():
    """429 on every attempt (retries exhausted) is transient — the next run retries."""
    resp_429 = _mock_response(429, headers={"Retry-After": "0"})
    resp_429.text = "rate limited"
    with patch("requests.post", return_value=resp_429) as mock_post, \
         patch("time.sleep"):
        with pytest.raises(TokenRefreshTransient, match="429"):
            refresh_access_token("rt")
    # 1 initial + 2 retries = 3 POSTs
    assert mock_post.call_count == 3


def test_refresh_access_token_request_shape():
    """Assert exact URL, JSON body, and absence of any Authorization header."""
    mock_resp = _mock_response(200, {"access_token": "at", "refresh_token": "rt2"})
    with patch("requests.post", return_value=mock_resp) as mock_post:
        refresh_access_token("my-rt")
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.granola.ai/v1/refresh-access-token"
    assert kwargs["json"] == {"refresh_token": "my-rt"}
    headers = kwargs["headers"]
    assert not any(k.lower() == "authorization" for k in headers)
    assert headers["X-Granola-Platform"] == "macos"


def test_refresh_access_token_429_then_success_retries():
    resp_429 = _mock_response(429, headers={"Retry-After": "0"})
    resp_429.text = "rate limited"
    resp_200 = _mock_response(200, {"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600})
    with patch("requests.post", side_effect=[resp_429, resp_200]) as mock_post, \
         patch("time.sleep") as mock_sleep:
        result = refresh_access_token("my-rt")
    assert result["access_token"] == "new-at"
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once_with(0.0)


def test_refresh_access_token_missing_refresh_token_in_response_keeps_original():
    mock_resp = _mock_response(200, {"access_token": "new-at", "expires_in": 3600})
    with patch("requests.post", return_value=mock_resp):
        result = refresh_access_token("original-rt")
    assert result["refresh_token"] == "original-rt"


# ---------------------------------------------------------------------------
# Token store: read/write roundtrip and file permissions
# ---------------------------------------------------------------------------


def test_write_and_read_token_store_roundtrip(token_dir):
    token_dir.mkdir(parents=True, exist_ok=True)
    path = token_dir / "test.json"
    _write_token_store(path, "at-val", "rt-val", 9999999.0)
    data = _read_token_store(path)
    assert data is not None
    assert data["access_token"] == "at-val"
    assert data["refresh_token"] == "rt-val"
    assert data["expiry"] == 9999999.0


def test_write_token_store_atomic_file_permissions(token_dir):
    token_dir.mkdir(parents=True, exist_ok=True)
    path = token_dir / "perms.json"
    _write_token_store(path, "at", "rt", 1234.0)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# get_access_token_for
# ---------------------------------------------------------------------------


def test_get_access_token_for_fresh_token_no_refresh_call(token_dir):
    """Store has a fresh (far-future exp) JWT — return it without calling refresh."""
    fresh = _fresh_jwt()
    token_dir.mkdir(parents=True, exist_ok=True)
    store_path = token_dir / "test_example_com.json"
    _write_token_store(store_path, fresh, "stored-rt", float(int(time.time()) + 3600))

    acc = _make_account(email="test@example.com", access_token="old-at", refresh_token="unused-rt")
    with patch("notesync.tokens.refresh_access_token") as mock_refresh:
        result = get_access_token_for(acc)
    assert result == fresh
    mock_refresh.assert_not_called()


def test_get_access_token_for_stale_token_refreshes(token_dir):
    """Store has a stale token — calls refresh once and persists new token."""
    stale = _stale_jwt()
    token_dir.mkdir(parents=True, exist_ok=True)
    store_path = token_dir / "test_example_com.json"
    _write_token_store(store_path, stale, "stored-rt", 100.0)

    new_fresh = _fresh_jwt()
    mock_result = {"access_token": new_fresh, "refresh_token": "new-rt", "expires_in": 3600}
    acc = _make_account(email="test@example.com", access_token=stale, refresh_token="account-rt")
    with patch("notesync.tokens.refresh_access_token", return_value=mock_result) as mock_refresh:
        result = get_access_token_for(acc)
    assert result == new_fresh
    mock_refresh.assert_called_once_with("stored-rt")

    # Verify persisted
    store = _read_token_store(store_path)
    assert store is not None
    assert store["access_token"] == new_fresh
    assert store["refresh_token"] == "new-rt"


def test_get_access_token_for_bootstrap_first_run(token_dir):
    """No store file, account has stale on-disk token + refresh_token — calls refresh once."""
    stale = _stale_jwt()
    new_fresh = _fresh_jwt()
    mock_result = {"access_token": new_fresh, "refresh_token": "new-rt", "expires_in": 3600}
    acc = _make_account(email="test@example.com", access_token=stale, refresh_token="bootstrap-rt")
    store_path = token_dir / "test_example_com.json"

    assert not store_path.exists()
    with patch("notesync.tokens.refresh_access_token", return_value=mock_result) as mock_refresh:
        result = get_access_token_for(acc)
    assert result == new_fresh
    mock_refresh.assert_called_once_with("bootstrap-rt")
    assert store_path.exists()


def test_get_access_token_for_bootstrap_uses_account_rt_only_once(token_dir):
    """First call: stale on-disk token bootstraps via refresh. Second call reads fresh store."""
    stale = _stale_jwt()
    new_fresh = _fresh_jwt()
    mock_result = {"access_token": new_fresh, "refresh_token": "new-rt", "expires_in": 3600}
    acc = _make_account(email="test@example.com", access_token=stale, refresh_token="bootstrap-rt")

    with patch("notesync.tokens.refresh_access_token", return_value=mock_result) as mock_refresh:
        r1 = get_access_token_for(acc)
        r2 = get_access_token_for(acc)
    assert r1 == new_fresh
    assert r2 == new_fresh
    mock_refresh.assert_called_once()


def test_get_access_token_for_no_refresh_token_raises(token_dir):
    """No store file, stale on-disk token, no refresh_token — raises TokenRefreshError."""
    stale = _stale_jwt()
    acc = GranolaAccount(email="test@example.com", access_token=stale, refresh_token=None)
    with pytest.raises(TokenRefreshError, match="no refresh token available"):
        get_access_token_for(acc)


def test_get_access_token_for_fresh_on_disk_no_store_uses_directly(token_dir):
    """No store file, account has fresh on-disk token — returns it without refresh."""
    fresh = _fresh_jwt()
    acc = _make_account(email="test@example.com", access_token=fresh, refresh_token="rt")
    with patch("notesync.tokens.refresh_access_token") as mock_refresh:
        result = get_access_token_for(acc)
    assert result == fresh
    mock_refresh.assert_not_called()


# ---------------------------------------------------------------------------
# CLI: sync stale-token behavior
# ---------------------------------------------------------------------------


def _run_sync_with(side_effect, tmp_path, monkeypatch):
    """Invoke `notesync sync` with a single account, patching get_access_token_for."""
    import json as _json
    from click.testing import CliRunner
    from notesync.cli import cli
    from notesync.auth import GranolaAuth as _GranolaAuth

    supabase = tmp_path / "supabase.json"
    stored_accounts = tmp_path / "stored-accounts.json"
    monkeypatch.setattr(_GranolaAuth, "get_supabase_config_path", staticmethod(lambda: str(supabase)))
    monkeypatch.setattr(_GranolaAuth, "_get_stored_accounts_path", staticmethod(lambda: str(stored_accounts)))

    account_entry = {
        "userId": "u1",
        "email": "acct@example.com",
        "tokens": _json.dumps({"access_token": "at", "refresh_token": "rt"}),
    }
    stored_accounts.write_text(_json.dumps({"accounts": _json.dumps([account_entry])}))

    output_dir = tmp_path / "notes"
    output_dir.mkdir()

    with patch("notesync.cli.get_access_token_for", side_effect=side_effect):
        return CliRunner().invoke(cli, ["sync", str(output_dir)])


def test_sync_dead_refresh_token_emits_stale_prefix(tmp_path, monkeypatch):
    """A dead refresh token emits the greppable Stale-token: marker and exits non-zero."""
    result = _run_sync_with(TokenRefreshDead("invalid_grant"), tmp_path, monkeypatch)
    assert result.exit_code != 0
    assert "Stale-token:" in result.output


def test_sync_transient_error_does_not_emit_stale_prefix(tmp_path, monkeypatch):
    """A transient refresh error must NOT emit Stale-token: (no false re-open alert)."""
    result = _run_sync_with(TokenRefreshTransient("429 rate limited"), tmp_path, monkeypatch)
    assert result.exit_code != 0
    assert "Stale-token:" not in result.output
    assert "transient" in result.output.lower()


def test_sync_persist_error_emits_distinct_non_stale_marker(tmp_path, monkeypatch):
    """A persist error is high-severity: distinct marker, NOT Stale-token, non-zero exit."""
    result = _run_sync_with(TokenPersistError("could not write store"), tmp_path, monkeypatch)
    assert result.exit_code != 0
    assert "Token-persist-error:" in result.output
    assert "Stale-token:" not in result.output


# ---------------------------------------------------------------------------
# Single-use rotation across two real spends
# ---------------------------------------------------------------------------


def test_single_use_rotation_across_two_spends(token_dir):
    """
    Prove real rotation: each spend uses the previously-rotated token, never an
    earlier one. Spend 1 uses rt1 -> store holds rt2; spend 2 uses rt2 (NEVER rt1).
    """
    token_dir.mkdir(parents=True, exist_ok=True)
    store_path = token_dir / "acct_example_com.json"
    acc = _make_account(email="acct@example.com", access_token="unused", refresh_token="plaintext-rt")

    # Seed a stale store holding rt1.
    _write_token_store(store_path, _stale_jwt(), "rt1", 100.0)

    fresh1 = _fresh_jwt()
    fresh2 = _fresh_jwt()

    def fake_refresh(rt):
        if rt == "rt1":
            return {"access_token": fresh1, "refresh_token": "rt2", "expires_in": 3600}
        if rt == "rt2":
            return {"access_token": fresh2, "refresh_token": "rt3", "expires_in": 3600}
        raise AssertionError(f"unexpected refresh token presented: {rt!r}")

    with patch("notesync.tokens.refresh_access_token", side_effect=fake_refresh) as mock_refresh:
        r1 = get_access_token_for(acc)
    assert r1 == fresh1
    mock_refresh.assert_called_once_with("rt1")
    assert _read_token_store(store_path)["refresh_token"] == "rt2"

    # Force the store stale again and spend a second time.
    store = _read_token_store(store_path)
    _write_token_store(store_path, _stale_jwt(), store["refresh_token"], 100.0)

    with patch("notesync.tokens.refresh_access_token", side_effect=fake_refresh) as mock_refresh:
        r2 = get_access_token_for(acc)
    assert r2 == fresh2
    mock_refresh.assert_called_once_with("rt2")  # never rt1
    assert _read_token_store(store_path)["refresh_token"] == "rt3"


# ---------------------------------------------------------------------------
# Persist-after-spend safety
# ---------------------------------------------------------------------------


def test_persist_failure_after_spend_raises_token_persist_error(token_dir):
    """
    If the write fails after a successful refresh, _persist raises TokenPersistError
    (distinct from TokenRefreshError). Retries the write before giving up.
    """
    store_path = token_dir / "x.json"
    result = {"access_token": _fresh_jwt(), "refresh_token": "rot-rt", "expires_in": 3600}
    with patch("notesync.tokens._write_token_store", side_effect=OSError("disk full")) as mock_write:
        with pytest.raises(TokenPersistError, match="could not persist"):
            _persist(store_path, result)
    assert mock_write.call_count == 3  # 1 + 2 retries
    assert not isinstance(TokenPersistError(), TokenRefreshError)


def test_get_access_token_for_persist_failure_leaves_old_store(token_dir):
    """
    Documents the danger window: on persist failure the on-disk store still holds
    the OLD (now-spent) token, and TokenPersistError propagates so a human intervenes.
    """
    token_dir.mkdir(parents=True, exist_ok=True)
    store_path = token_dir / "acct_example_com.json"
    _write_token_store(store_path, _stale_jwt(), "old-rt", 100.0)
    acc = _make_account(email="acct@example.com", access_token="unused", refresh_token="pt-rt")

    mock_result = {"access_token": _fresh_jwt(), "refresh_token": "new-rt", "expires_in": 3600}
    with patch("notesync.tokens.refresh_access_token", return_value=mock_result), \
         patch("notesync.tokens._write_token_store", side_effect=OSError("disk full")):
        with pytest.raises(TokenPersistError):
            get_access_token_for(acc)

    # Store still holds the old token (the rotated one was lost).
    assert _read_token_store(store_path)["refresh_token"] == "old-rt"


# ---------------------------------------------------------------------------
# Empty-refresh-token wedge
# ---------------------------------------------------------------------------


def test_bootstrap_fresh_on_disk_no_rt_does_not_persist_empty(token_dir):
    """
    Fresh on-disk access token but no refresh_token: return it WITHOUT writing a
    store file (an empty-rt store would wedge the account later).
    """
    fresh = _fresh_jwt()
    acc = GranolaAccount(email="acct@example.com", access_token=fresh, refresh_token=None)
    store_path = token_dir / "acct_example_com.json"

    with patch("notesync.tokens.refresh_access_token") as mock_refresh:
        result = get_access_token_for(acc)
    assert result == fresh
    mock_refresh.assert_not_called()
    assert not store_path.exists()


def test_stale_store_empty_rt_falls_back_to_account_rt(token_dir):
    """
    Store exists but its refresh_token is empty: fall back to the account's
    plaintext refresh token rather than wedging.
    """
    token_dir.mkdir(parents=True, exist_ok=True)
    store_path = token_dir / "acct_example_com.json"
    _write_token_store(store_path, _stale_jwt(), "", 100.0)  # empty stored rt
    acc = _make_account(email="acct@example.com", access_token="unused", refresh_token="account-rt")

    new_fresh = _fresh_jwt()
    mock_result = {"access_token": new_fresh, "refresh_token": "rotated", "expires_in": 3600}
    with patch("notesync.tokens.refresh_access_token", return_value=mock_result) as mock_refresh:
        result = get_access_token_for(acc)
    assert result == new_fresh
    mock_refresh.assert_called_once_with("account-rt")


def test_stale_store_and_account_both_empty_rt_raises_dead(token_dir):
    """If both the store and the account have no refresh token, that's dead."""
    token_dir.mkdir(parents=True, exist_ok=True)
    store_path = token_dir / "acct_example_com.json"
    _write_token_store(store_path, _stale_jwt(), "", 100.0)
    acc = GranolaAccount(email="acct@example.com", access_token=_stale_jwt(), refresh_token=None)
    with pytest.raises(TokenRefreshDead):
        get_access_token_for(acc)


def test_expires_in_clamped_on_non_jwt_token(token_dir):
    """A non-JWT access token uses clamped expires_in for the stored expiry."""
    token_dir.mkdir(parents=True, exist_ok=True)
    store_path = token_dir / "clamp.json"
    # Huge expires_in should clamp to 86400 (1 day).
    result = {"access_token": "opaque-not-a-jwt", "refresh_token": "rt", "expires_in": 999999999}
    before = time.time()
    _persist(store_path, result)
    store = _read_token_store(store_path)
    assert store["expiry"] <= before + 86400 + 5
