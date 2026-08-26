import json
import os
import socket
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from notesync.auth import GranolaAuth
from notesync.cli import cli
from notesync.device_auth import (
    DEVICE_AUTHORIZE_URL,
    DEVICE_CLIENT_ID,
    DEVICE_GRANT_TYPE,
    DEVICE_TOKEN_URL,
    DeviceCode,
    DeviceSession,
    DeviceSessionPersistError,
    DeviceSessionSecurityError,
    DeviceSessionTransient,
    _rotation_path,
    get_device_access_token,
    list_device_sessions,
    poll_device_token,
    request_device_code,
    save_device_session,
)


@pytest.fixture
def auth_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    supabase = tmp_path / "supabase.json"
    stored_accounts = tmp_path / "stored-accounts.json"
    monkeypatch.setattr(
        GranolaAuth, "get_supabase_config_path", staticmethod(lambda: str(supabase))
    )
    monkeypatch.setattr(
        GranolaAuth,
        "_get_stored_accounts_path",
        staticmethod(lambda: str(stored_accounts)),
    )
    return supabase, stored_accounts


def _response(status: int, payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    response.text = json.dumps(payload)
    return response


def _session(
    *,
    email: str = "person@example.com",
    access_token: str = "access",
    refresh_token: str = "refresh",
    expiry: float | None = None,
) -> DeviceSession:
    now = time.time()
    return DeviceSession(
        email=email,
        user_id="user-1",
        access_token=access_token,
        refresh_token=refresh_token,
        obtained_at=now,
        expiry=expiry if expiry is not None else now + 3600,
    )


def test_request_device_code_uses_form_grant_without_authorization_header():
    response = _response(
        200,
        {
            "device_code": "device-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://auth.example/activate",
            "verification_uri_complete": "https://auth.example/activate?code=ABCD",
            "expires_in": 300,
            "interval": 5,
        },
    )
    with patch("notesync.device_auth.requests.post", return_value=response) as post:
        code = request_device_code()

    assert code.user_code == "ABCD-EFGH"
    args, kwargs = post.call_args
    assert args[0] == DEVICE_AUTHORIZE_URL
    assert kwargs["data"] == {"client_id": DEVICE_CLIENT_ID}
    assert not any(key.lower() == "authorization" for key in kwargs["headers"])
    assert kwargs["headers"]["X-Granola-Platform"] == "darwin"


def test_poll_device_token_handles_pending_then_approval():
    code = DeviceCode(
        device_code="device-secret",
        user_code="ABCD",
        verification_uri="https://auth.example/activate",
        verification_uri_complete=None,
        expires_in=300,
        interval=1,
    )
    pending = _response(400, {"error": "authorization_pending"})
    approved = _response(
        200,
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
            "user": {"id": "user-1", "email": "person@example.com"},
        },
    )
    with patch("notesync.device_auth.requests.post", side_effect=[pending, approved]) as post, patch(
        "notesync.device_auth.time.sleep"
    ):
        session = poll_device_token(code)

    assert session.email == "person@example.com"
    assert session.access_token == "access"
    assert session.refresh_token == "refresh"
    assert post.call_count == 2
    _, kwargs = post.call_args
    assert kwargs["data"]["client_id"] == DEVICE_CLIENT_ID
    assert kwargs["data"]["grant_type"] == DEVICE_GRANT_TYPE
    assert kwargs["data"]["device_code"] == "device-secret"
    assert post.call_args.args[0] == DEVICE_TOKEN_URL


def test_save_and_list_session_uses_owner_only_permissions():
    session = _session()
    path = save_device_session(session)

    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    loaded = list_device_sessions()
    assert len(loaded) == 1
    assert loaded[0].email == session.email
    assert "access" not in repr(loaded[0])
    assert "refresh" not in repr(loaded[0])


def test_session_reader_refuses_group_readable_file():
    path = save_device_session(_session())
    os.chmod(path, 0o644)

    with pytest.raises(DeviceSessionSecurityError, match="permissions are too broad"):
        list_device_sessions()


def test_device_sessions_are_authoritative_over_desktop_files(auth_paths):
    supabase, stored_accounts = auth_paths
    supabase.write_text(json.dumps({"workos_tokens": {"access_token": "desktop"}}))
    stored_accounts.write_text(json.dumps({"accounts": json.dumps([])}))
    save_device_session(_session(access_token="device"))

    accounts = GranolaAuth.list_accounts()

    assert len(accounts) == 1
    assert accounts[0].source == "device-auth"
    assert accounts[0].access_token == "device"


def test_fresh_device_session_makes_no_network_call():
    save_device_session(_session(access_token="fresh"))

    with patch("notesync.device_auth.requests.post") as post:
        token = get_device_access_token("person@example.com")

    assert token == "fresh"
    post.assert_not_called()


def test_expired_device_session_refreshes_once_and_persists_rotation():
    save_device_session(_session(access_token="old", refresh_token="old-refresh", expiry=1))
    response = _response(
        200,
        {
            "access_token": "new",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        },
    )
    with patch("notesync.device_auth.requests.post", return_value=response) as post:
        token = get_device_access_token("person@example.com")

    assert token == "new"
    assert post.call_count == 1
    assert post.call_args.kwargs["json"] == {"refresh_token": "old-refresh"}
    assert list_device_sessions()[0].refresh_token == "new-refresh"
    assert not _rotation_path("person@example.com").exists()


def test_ambiguous_refresh_failure_leaves_rotation_marker_and_blocks_retry():
    save_device_session(_session(expiry=1))
    response = _response(500, {"error": "server_error"})
    with patch("notesync.device_auth.requests.post", return_value=response):
        with pytest.raises(DeviceSessionPersistError, match="rotation is uncertain"):
            get_device_access_token("person@example.com")

    assert _rotation_path("person@example.com").exists()
    with patch("notesync.device_auth.requests.post") as post:
        with pytest.raises(DeviceSessionPersistError, match="interrupted token rotation"):
            get_device_access_token("person@example.com")
    post.assert_not_called()


@pytest.mark.parametrize(
    "error, expected_type",
    [
        (requests.ConnectTimeout("connect timed out"), "ConnectTimeout"),
        (
            requests.ConnectionError(socket.gaierror(-2, "name resolution failed")),
            "gaierror",
        ),
    ],
)
def test_pre_send_refresh_failure_is_retryable_and_clears_marker(
    error: requests.RequestException, expected_type: str
):
    save_device_session(_session(expiry=1))

    with patch("notesync.device_auth.requests.post", side_effect=error):
        with pytest.raises(
            DeviceSessionTransient,
            match="failed before the request was sent",
        ) as raised:
            get_device_access_token("person@example.com")

    assert expected_type in str(raised.value)
    assert not _rotation_path("person@example.com").exists()


def test_read_timeout_remains_ambiguous_and_preserves_marker():
    save_device_session(_session(expiry=1))

    with patch(
        "notesync.device_auth.requests.post",
        side_effect=requests.ReadTimeout("response timed out"),
    ):
        with pytest.raises(DeviceSessionPersistError, match="ReadTimeout"):
            get_device_access_token("person@example.com")

    assert _rotation_path("person@example.com").exists()


def test_rate_limited_refresh_removes_rotation_marker_for_safe_retry():
    save_device_session(_session(expiry=1))
    response = _response(429, {"error": "rate_limited"})
    with patch("notesync.device_auth.requests.post", return_value=response):
        with pytest.raises(DeviceSessionTransient, match="rate-limited"):
            get_device_access_token("person@example.com")

    assert not _rotation_path("person@example.com").exists()


def test_auth_login_status_and_logout_commands():
    code = DeviceCode(
        device_code="secret",
        user_code="ABCD",
        verification_uri="https://auth.example/activate",
        verification_uri_complete=None,
        expires_in=300,
        interval=5,
    )
    session = _session()
    runner = CliRunner()
    with patch("notesync.cli.request_device_code", return_value=code), patch(
        "notesync.cli.poll_device_token", return_value=session
    ), patch("notesync.cli.webbrowser.open", return_value=True):
        login = runner.invoke(cli, ["auth", "login"])

    assert login.exit_code == 0, login.output
    assert "Authorized person@example.com" in login.output

    status = runner.invoke(cli, ["auth", "status", "--json"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["sessions"][0]["email"] == "person@example.com"

    logout = runner.invoke(cli, ["auth", "logout", "--account", "person@example.com"])
    assert logout.exit_code == 0, logout.output
    assert "Removed local CLI session" in logout.output
    assert list_device_sessions() == []
