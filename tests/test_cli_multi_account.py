"""
Integration tests for the multi-account sync CLI.

These tests exercise the CLI's account discovery, --account filtering, and
the legacy-DB migration guard. The actual export is mocked so the tests
never hit the network or filesystem-heavy export pipeline — they verify
that the *right* engine is constructed with the *right* per-account
directory and token, in the *right* order.
"""

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from notesync.auth import GranolaAccount
from notesync.cli import _account_subdir, cli
from notesync.sync import SYNC_DB_FILENAME


def _jwt_with_exp(exp: int) -> str:
    """Build a JWT-shaped string whose payload carries the given `exp` claim.
    Stale-token tests need a real JWT shape so `is_token_expired` decodes it
    — the literal token strings used elsewhere in this file are not JWTs and
    would be treated as 'fresh' (undecodable)."""
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{b64({'alg':'none'})}.{b64({'exp': exp})}."


# Far past / far future relative to any wall-clock time we might run under.
EXPIRED_JWT = _jwt_with_exp(1)  # 1970
FRESH_JWT = _jwt_with_exp(9_999_999_999)  # year 2286


@pytest.fixture
def two_accounts():
    return [
        GranolaAccount(email="alice@example.com", access_token="tok-alice", user_id="u-a"),
        GranolaAccount(email="bob@work.io", access_token="tok-bob", user_id="u-b"),
    ]


def _mock_engine_factory():
    """Returns (factory_callable, list_of_engine_mocks_keyed_by_token)."""
    engines = []

    class _FakeEngine:
        def __init__(self, api=None):
            # The CLI passes a GranolaAPI(access_token=...) here. Snapshot the
            # token so assertions can confirm the right account drove this call.
            self.api = api
            self.sync_calls = []
            engines.append(self)

        def sync_all_notes(self, **kwargs):
            self.sync_calls.append(kwargs)
            return {"total": 0, "new": 0, "updated": 0, "skipped": 0, "failed": 0}

    return _FakeEngine, engines


def test_sync_iterates_all_accounts_by_default(tmp_path: Path, two_accounts):
    output_dir = tmp_path / "notes"
    output_dir.mkdir()

    FakeEngine, engines = _mock_engine_factory()
    fake_apis = []

    def fake_api(access_token=None):
        api = MagicMock()
        api.access_token = access_token
        fake_apis.append(api)
        return api

    with patch("notesync.cli.GranolaAuth.list_accounts", return_value=two_accounts), \
         patch("notesync.cli.GranolaAPI", side_effect=fake_api), \
         patch("notesync.cli.ExportEngine", FakeEngine):
        result = CliRunner().invoke(cli, ["sync", str(output_dir)])

    assert result.exit_code == 0, result.output
    # Both accounts synced, each with their own engine + per-email subdir.
    assert len(engines) == 2
    expected_subdirs = [
        str(output_dir / "alice_example_com"),
        str(output_dir / "bob_work_io"),
    ]
    actual_subdirs = [e.sync_calls[0]["output_dir"] for e in engines]
    assert actual_subdirs == expected_subdirs
    # Each engine received the right token via its API.
    assert [api.access_token for api in fake_apis] == ["tok-alice", "tok-bob"]


def test_sync_filters_by_account_flag(tmp_path: Path, two_accounts):
    output_dir = tmp_path / "notes"
    output_dir.mkdir()

    FakeEngine, engines = _mock_engine_factory()
    fake_apis = []

    def fake_api(access_token=None):
        api = MagicMock()
        api.access_token = access_token
        fake_apis.append(api)
        return api

    with patch("notesync.cli.GranolaAuth.list_accounts", return_value=two_accounts), \
         patch("notesync.cli.GranolaAPI", side_effect=fake_api), \
         patch("notesync.cli.ExportEngine", FakeEngine):
        result = CliRunner().invoke(
            cli, ["sync", str(output_dir), "--account", "bob@work.io"]
        )

    assert result.exit_code == 0, result.output
    assert len(engines) == 1
    assert engines[0].sync_calls[0]["output_dir"] == str(output_dir / "bob_work_io")
    assert fake_apis[0].access_token == "tok-bob"


def test_sync_filter_is_case_insensitive(tmp_path: Path, two_accounts):
    output_dir = tmp_path / "notes"
    output_dir.mkdir()

    FakeEngine, engines = _mock_engine_factory()

    with patch("notesync.cli.GranolaAuth.list_accounts", return_value=two_accounts), \
         patch("notesync.cli.GranolaAPI", side_effect=lambda access_token=None: MagicMock(access_token=access_token)), \
         patch("notesync.cli.ExportEngine", FakeEngine):
        result = CliRunner().invoke(
            cli, ["sync", str(output_dir), "--account", "ALICE@EXAMPLE.COM"]
        )

    assert result.exit_code == 0, result.output
    assert len(engines) == 1
    assert engines[0].sync_calls[0]["output_dir"] == str(output_dir / "alice_example_com")


def test_sync_unknown_account_errors_and_lists_available(tmp_path: Path, two_accounts):
    FakeEngine, engines = _mock_engine_factory()

    with patch("notesync.cli.GranolaAuth.list_accounts", return_value=two_accounts), \
         patch("notesync.cli.GranolaAPI", MagicMock()), \
         patch("notesync.cli.ExportEngine", FakeEngine):
        result = CliRunner().invoke(
            cli, ["sync", str(tmp_path / "notes"), "--account", "nobody@nowhere"]
        )

    assert result.exit_code == 1
    # Available accounts must be surfaced so the user can correct the typo.
    assert "alice@example.com" in result.output
    assert "bob@work.io" in result.output
    assert len(engines) == 0


def test_sync_refuses_legacy_root_level_db(tmp_path: Path, two_accounts):
    """
    If a pre-multi-account .notesync-sync.db sits at OUTPUT_DIR root, sync
    must refuse to run and tell the user how to migrate. Silently starting
    to write to <email>/ subdirs would orphan all previously-synced files
    from their tracking DB and cause mass re-export on next run.
    """
    output_dir = tmp_path / "notes"
    output_dir.mkdir()
    (output_dir / SYNC_DB_FILENAME).write_bytes(b"legacy")  # presence is what matters

    FakeEngine, engines = _mock_engine_factory()

    with patch("notesync.cli.GranolaAuth.list_accounts", return_value=two_accounts), \
         patch("notesync.cli.GranolaAPI", MagicMock()), \
         patch("notesync.cli.ExportEngine", FakeEngine):
        result = CliRunner().invoke(cli, ["sync", str(output_dir)])

    assert result.exit_code == 1
    assert "legacy single-account layout" in result.output
    assert SYNC_DB_FILENAME in result.output
    # No engines should have been constructed — migration must happen first.
    assert len(engines) == 0


def test_account_subdir_sanitizes_email():
    assert _account_subdir(GranolaAccount(email="a.b+tag@x.co", access_token="t")) == "a_b_tag_x_co"
    assert _account_subdir(GranolaAccount(email="ALICE@EXAMPLE.COM", access_token="t")) == "alice_example_com"
    assert _account_subdir(GranolaAccount(email="weird---chars!!!@x", access_token="t")) == "weird_chars_x"


def test_sync_refuses_when_subdir_collision_detected(tmp_path: Path):
    """
    `a.b@x.co` and `a-b@x.co` both sanitize to `a_b_x_co`. Silently merging
    them into one subdir would intermix sync DBs across distinct identities.
    """
    collide = [
        GranolaAccount(email="a.b@x.co", access_token="t1", user_id="u1"),
        GranolaAccount(email="a-b@x.co", access_token="t2", user_id="u2"),
    ]
    FakeEngine, engines = _mock_engine_factory()

    with patch("notesync.cli.GranolaAuth.list_accounts", return_value=collide), \
         patch("notesync.cli.GranolaAPI", MagicMock()), \
         patch("notesync.cli.ExportEngine", FakeEngine):
        result = CliRunner().invoke(cli, ["sync", str(tmp_path / "notes")])

    assert result.exit_code == 1
    assert "email collision" in result.output
    assert "a.b@x.co" in result.output
    assert "a-b@x.co" in result.output
    assert len(engines) == 0


def test_accounts_command_table_output(two_accounts):
    """Table form should surface email + sanitized subdir for every account."""
    with patch("notesync.cli.GranolaAuth.list_accounts", return_value=two_accounts):
        result = CliRunner().invoke(cli, ["accounts"])

    assert result.exit_code == 0, result.output
    assert "alice@example.com" in result.output
    assert "bob@work.io" in result.output
    assert "alice_example_com" in result.output
    assert "bob_work_io" in result.output


def test_accounts_command_json_output(two_accounts):
    """JSON shape is a scripting contract — assert the exact fields."""
    import json as _json

    with patch("notesync.cli.GranolaAuth.list_accounts", return_value=two_accounts):
        result = CliRunner().invoke(cli, ["accounts", "--json"])

    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["count"] == 2
    entries = {entry["email"]: entry for entry in payload["accounts"]}
    assert set(entries) == {"alice@example.com", "bob@work.io"}
    alice = entries["alice@example.com"]
    assert alice["subdir"] == "alice_example_com"
    assert alice["user_id"] == "u-a"
    assert alice["source"] == "stored-accounts"


def test_sync_skips_account_with_expired_token_and_alerts(tmp_path: Path):
    """
    Most common production failure: one inactive account's JWT has aged past
    its 6h TTL because Granola was closed overnight. The active account must
    still sync, but the run must exit 1 with a recognizable marker line so
    the cron wrapper routes the alert to the `stale` category — the user
    explicitly wants to be told even when only one account is stale.
    """
    output_dir = tmp_path / "notes"
    output_dir.mkdir()
    mixed = [
        GranolaAccount(email="stale@example.com", access_token=EXPIRED_JWT, user_id="u1"),
        GranolaAccount(email="fresh@example.com", access_token=FRESH_JWT, user_id="u2"),
    ]

    FakeEngine, engines = _mock_engine_factory()

    with patch("notesync.cli.GranolaAuth.list_accounts", return_value=mixed), \
         patch("notesync.cli.GranolaAPI", side_effect=lambda access_token=None: MagicMock(access_token=access_token)), \
         patch("notesync.cli.ExportEngine", FakeEngine):
        result = CliRunner().invoke(cli, ["sync", str(output_dir)])

    # Exit 1 so the wrapper alerts; the fresh account still synced.
    assert result.exit_code == 1, result.output
    assert len(engines) == 1
    assert engines[0].sync_calls[0]["output_dir"] == str(output_dir / "fresh_example_com")
    # Marker line is the contract with the wrapper's `stale` category check.
    assert "Stale-token: stale@example.com" in result.output
    # And NOT the "Error syncing …: 401" prose that would route to auth.
    assert "Error syncing stale@example.com" not in result.output


def test_sync_all_stale_exits_non_zero(tmp_path: Path):
    """All accounts stale: no engines run, exit 1, every account surfaced on
    its own Stale-token line so the wrapper still maps to `stale`."""
    output_dir = tmp_path / "notes"
    output_dir.mkdir()
    all_stale = [
        GranolaAccount(email="a@x.com", access_token=EXPIRED_JWT, user_id="u1"),
        GranolaAccount(email="b@x.com", access_token=EXPIRED_JWT, user_id="u2"),
    ]

    FakeEngine, engines = _mock_engine_factory()

    with patch("notesync.cli.GranolaAuth.list_accounts", return_value=all_stale), \
         patch("notesync.cli.GranolaAPI", MagicMock()), \
         patch("notesync.cli.ExportEngine", FakeEngine):
        result = CliRunner().invoke(cli, ["sync", str(output_dir)])

    assert result.exit_code == 1
    assert len(engines) == 0
    assert "Stale-token: a@x.com" in result.output
    assert "Stale-token: b@x.com" in result.output


def test_sync_one_account_failure_does_not_block_others(tmp_path: Path, two_accounts):
    """
    Common case: one stale token in a multi-account setup. The healthy
    account must still sync; exit non-zero (so the wrapper alerts) but
    don't abort mid-loop.
    """
    output_dir = tmp_path / "notes"
    output_dir.mkdir()

    engines: list = []

    class _FailingThenOkEngine:
        def __init__(self, api=None):
            self.api = api
            engines.append(self)
            self.is_first = len(engines) == 1

        def sync_all_notes(self, **_kwargs):
            if self.is_first:
                raise RuntimeError("simulated token expiry")
            return {"total": 0}

    with patch("notesync.cli.GranolaAuth.list_accounts", return_value=two_accounts), \
         patch("notesync.cli.GranolaAPI", side_effect=lambda access_token=None: MagicMock(access_token=access_token)), \
         patch("notesync.cli.ExportEngine", _FailingThenOkEngine):
        result = CliRunner().invoke(cli, ["sync", str(output_dir)])

    assert len(engines) == 2, "second account must still attempt sync after first failed"
    assert "alice@example.com" in result.output
    assert "simulated token expiry" in result.output
    assert "bob@work.io" in result.output
    assert result.exit_code == 1  # non-zero so the wrapper alerts
