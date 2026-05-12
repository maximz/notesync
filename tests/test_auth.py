import json
from pathlib import Path

import pytest

from notesync.auth import GranolaAuth


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


def _stored_accounts_payload(access_token: str) -> dict:
    """Build a `stored-accounts.json` body with the double-stringified shape."""
    tokens = json.dumps({"access_token": access_token, "refresh_token": "rt"})
    account = {"userId": "u", "email": "e@example.com", "tokens": tokens}
    return {"accounts": json.dumps([account])}


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
