"""Granola device authorization and CLI-owned session persistence.

The desktop application's refresh token is deliberately never read or rotated
by this module.  ``auth login`` creates a separate, user-approved token family
and stores it in an owner-only directory for later unattended syncs.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import fcntl
import requests

from .api import API_CONFIG, DEFAULT_REQUEST_TIMEOUT, get_user_agent
from .auth import is_token_expired, jwt_expires_at


DEVICE_AUTHORIZE_URL = "https://auth.granola.ai/user_management/authorize/device"
DEVICE_TOKEN_URL = "https://auth.granola.ai/user_management/authenticate"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEVICE_CLIENT_ID = "client_01JZJ0XBDAT8PHJWQY09Y0VD61"
SESSION_SCHEMA_VERSION = 1


class DeviceAuthError(Exception):
    """Base class for device authorization and CLI-session failures."""


class DeviceCodeExpired(DeviceAuthError):
    """The device code expired before the operator approved it."""


class DeviceAuthDenied(DeviceAuthError):
    """The operator denied the device authorization request."""


class DeviceSessionSecurityError(DeviceAuthError):
    """A session path or file has unsafe ownership/permission characteristics."""


class DeviceSessionDead(DeviceAuthError):
    """The session cannot be refreshed and requires a new ``auth login``."""


class DeviceSessionTransient(DeviceAuthError):
    """A retryable failure occurred before a refresh token was consumed."""


class DeviceSessionPersistError(DeviceAuthError):
    """A token rotation may have happened without a durable local write."""


@dataclass(frozen=True)
class DeviceCode:
    device_code: str = field(repr=False)
    user_code: str
    verification_uri: str
    verification_uri_complete: Optional[str]
    expires_in: int
    interval: int


@dataclass(frozen=True)
class DeviceSession:
    email: str
    user_id: Optional[str]
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    obtained_at: float
    expiry: float

    def to_json_dict(self) -> dict:
        return {
            "schema_version": SESSION_SCHEMA_VERSION,
            "source": "device-auth",
            "email": self.email,
            "user_id": self.user_id,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "obtained_at": self.obtained_at,
            "expiry": self.expiry,
        }


def _request_headers(*, content_type: str) -> dict[str, str]:
    return {
        "Content-Type": content_type,
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "User-Agent": get_user_agent(),
        "X-Client-Version": API_CONFIG["CLIENT_VERSION"],
        "X-Granola-Platform": "darwin",
    }


def request_device_code() -> DeviceCode:
    """Start Granola's browser-approved device authorization grant."""
    try:
        response = requests.post(
            DEVICE_AUTHORIZE_URL,
            data={"client_id": DEVICE_CLIENT_ID},
            headers=_request_headers(content_type="application/x-www-form-urlencoded"),
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise DeviceAuthError(f"device authorization request failed: {exc}") from exc

    if response.status_code != 200:
        raise DeviceAuthError(
            f"device authorization returned {response.status_code}: {response.text[:500]}"
        )

    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise DeviceAuthError("device authorization returned invalid JSON") from exc

    device_code = payload.get("device_code")
    user_code = payload.get("user_code")
    verification_uri = payload.get("verification_uri")
    if not device_code or not user_code or not verification_uri:
        raise DeviceAuthError("device authorization response was missing required fields")

    return DeviceCode(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=payload.get("verification_uri_complete"),
        expires_in=max(30, int(payload.get("expires_in") or 300)),
        interval=max(1, int(payload.get("interval") or 5)),
    )


def poll_device_token(device_code: DeviceCode) -> DeviceSession:
    """Poll until the device grant is approved, denied, or expires."""
    interval = float(device_code.interval)
    deadline = time.monotonic() + device_code.expires_in

    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            response = requests.post(
                DEVICE_TOKEN_URL,
                data={
                    "client_id": DEVICE_CLIENT_ID,
                    "grant_type": DEVICE_GRANT_TYPE,
                    "device_code": device_code.device_code,
                },
                headers=_request_headers(
                    content_type="application/x-www-form-urlencoded"
                ),
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise DeviceAuthError(f"device token request failed: {exc}") from exc

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = {}

        if response.status_code == 200:
            access_token = payload.get("access_token")
            refresh_token = payload.get("refresh_token")
            user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
            email = user.get("email")
            if not access_token or not refresh_token or not email:
                raise DeviceAuthError(
                    "approved device authorization omitted an access token, refresh token, or email"
                )
            obtained_at = time.time()
            jwt_expiry = jwt_expires_at(access_token)
            if jwt_expiry is not None:
                expiry = float(jwt_expiry)
            else:
                ttl = max(60, min(int(payload.get("expires_in") or 3600), 86400))
                expiry = obtained_at + ttl
            return DeviceSession(
                email=email,
                user_id=user.get("id"),
                access_token=access_token,
                refresh_token=refresh_token,
                obtained_at=obtained_at,
                expiry=expiry,
            )

        error_code = payload.get("error")
        if error_code == "authorization_pending":
            continue
        if error_code == "slow_down":
            interval += 5
            continue
        if error_code == "expired_token":
            raise DeviceCodeExpired("device authorization expired before approval")
        if error_code == "access_denied":
            raise DeviceAuthDenied("device authorization was denied")

        body = response.text[:500]
        raise DeviceAuthError(
            f"device token endpoint returned {response.status_code}: {body}"
        )

    raise DeviceCodeExpired("device authorization expired before approval")


def _session_dir() -> Path:
    return Path(
        os.environ.get(
            "NOTESYNC_SESSION_DIR",
            Path.home() / ".local" / "share" / "notesync" / "sessions",
        )
    )


def _normalized_email(email: str) -> str:
    return email.strip().lower()


def _session_stem(email: str) -> str:
    normalized = _normalized_email(email)
    readable = "".join(c if c.isalnum() else "_" for c in normalized).strip("_")
    readable = readable[:48] or "account"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return f"{readable}.{digest}"


def _session_path(email: str) -> Path:
    return _session_dir() / f"{_session_stem(email)}.json"


def _rotation_path(email: str) -> Path:
    return _session_dir() / f"{_session_stem(email)}.rotating"


def _lock_path(email: str) -> Path:
    return _session_dir() / f"{_session_stem(email)}.lock"


def _ensure_private_directory() -> Path:
    path = _session_dir()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DeviceSessionSecurityError(f"session directory is not a real directory: {path}")
    if info.st_uid != os.getuid():
        raise DeviceSessionSecurityError(f"session directory is not owned by this user: {path}")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(str(path), flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, payload: dict) -> None:
    directory = _ensure_private_directory()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(directory)
    except Exception:
        if fd >= 0:
            os.close(fd)
        tmp_path.unlink(missing_ok=True)
        raise


@contextmanager
def _session_lock(email: str) -> Iterator[None]:
    directory = _ensure_private_directory()
    path = _lock_path(email)
    fd = os.open(
        str(path),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        _fsync_directory(directory)


def _load_session_path(path: Path) -> DeviceSession:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise DeviceSessionSecurityError(f"session is not a regular file: {path}")
    if info.st_uid != os.getuid():
        raise DeviceSessionSecurityError(f"session is not owned by this user: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise DeviceSessionSecurityError(
            f"session permissions are too broad at {path}; expected mode 0600"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeviceAuthError(f"could not read device session at {path}: {exc}") from exc
    if payload.get("schema_version") != SESSION_SCHEMA_VERSION:
        raise DeviceAuthError(f"unsupported device-session schema at {path}")
    required = ("email", "access_token", "refresh_token", "obtained_at", "expiry")
    if any(not payload.get(key) for key in required):
        raise DeviceAuthError(f"device session is missing required fields at {path}")
    return DeviceSession(
        email=payload["email"],
        user_id=payload.get("user_id"),
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        obtained_at=float(payload["obtained_at"]),
        expiry=float(payload["expiry"]),
    )


def list_device_sessions() -> list[DeviceSession]:
    directory = _session_dir()
    if not directory.exists():
        return []
    _ensure_private_directory()
    sessions = [_load_session_path(path) for path in sorted(directory.glob("*.json"))]
    deduped: dict[str, DeviceSession] = {}
    for session in sessions:
        key = _normalized_email(session.email)
        current = deduped.get(key)
        if current is None or session.obtained_at > current.obtained_at:
            deduped[key] = session
    return sorted(deduped.values(), key=lambda session: session.email.lower())


def device_session_rotation_pending(email: str) -> bool:
    """True when a prior refresh may have consumed the stored token."""
    return _rotation_path(email).exists()


def save_device_session(session: DeviceSession) -> Path:
    """Durably store a newly approved session and clear any stale rotation marker."""
    with _session_lock(session.email):
        path = _session_path(session.email)
        _atomic_write(path, session.to_json_dict())
        marker = _rotation_path(session.email)
        marker.unlink(missing_ok=True)
        _fsync_directory(_session_dir())
        return path


def _find_session(email: str) -> tuple[Path, DeviceSession]:
    normalized = _normalized_email(email)
    path = _session_path(email)
    if path.exists():
        session = _load_session_path(path)
        if _normalized_email(session.email) == normalized:
            return path, session
    for candidate in list_device_sessions():
        if _normalized_email(candidate.email) == normalized:
            return _session_path(candidate.email), candidate
    raise DeviceSessionDead(f"no CLI-owned Granola session for {email}; run notesync auth login")


def _session_is_expired(session: DeviceSession) -> bool:
    if jwt_expires_at(session.access_token) is not None:
        return is_token_expired(session.access_token)
    return time.time() >= session.expiry - 60


class _KnownUnspentRefreshFailure(DeviceSessionTransient):
    """A refresh failure known not to have consumed the token."""


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """Walk wrapped transport errors without depending on urllib3 internals."""
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current

        wrapped = (
            current.__cause__,
            current.__context__,
            getattr(current, "reason", None),
            *current.args,
        )
        pending.extend(
            candidate
            for candidate in wrapped
            if isinstance(candidate, BaseException) and id(candidate) not in seen
        )


def _transport_failure_summary(exc: requests.RequestException) -> str:
    """Return token-free exception types and the innermost transport detail."""
    chain = list(_exception_chain(exc))
    type_names = list(dict.fromkeys(type(item).__name__ for item in chain))
    detail = str(chain[-1]).replace("\n", " ")[:240]
    summary = " -> ".join(type_names)
    return f"{summary}: {detail}" if detail else summary


def _refresh_was_definitely_not_sent(exc: requests.RequestException) -> bool:
    """True only for failures known to happen before an HTTP request is sent.

    Requests documents ``ConnectTimeout`` as safe to retry. DNS resolution and
    TCP connection establishment errors are likewise pre-request. Read timeouts,
    generic connection drops, proxy failures, and TLS errors remain ambiguous.
    urllib3 exception names are checked by name so Notesync remains compatible
    with both urllib3 1.x and 2.x supported by Requests.
    """
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return False
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if isinstance(
        exc,
        (
            requests.exceptions.InvalidSchema,
            requests.exceptions.InvalidURL,
            requests.exceptions.MissingSchema,
        ),
    ):
        return True

    pre_send_type_names = {
        "ConnectTimeoutError",
        "NameResolutionError",
        "NewConnectionError",
    }
    for item in _exception_chain(exc):
        if isinstance(item, (socket.gaierror, ConnectionRefusedError)):
            return True
        if type(item).__name__ in pre_send_type_names:
            return True
    return False


def _refresh_device_session(session: DeviceSession) -> DeviceSession:
    """Exchange one refresh token exactly once; the caller owns rotation safety."""
    url = f"{API_CONFIG['API_URL']}/refresh-access-token"
    try:
        response = requests.post(
            url,
            json={"refresh_token": session.refresh_token},
            headers=_request_headers(content_type="application/json"),
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        detail = _transport_failure_summary(exc)
        if _refresh_was_definitely_not_sent(exc):
            raise _KnownUnspentRefreshFailure(
                "Granola refresh failed before the request was sent; the token remains "
                f"safe to retry on the next run ({detail})"
            ) from exc
        raise DeviceSessionPersistError(
            "refresh response was lost after the request may have been sent; token rotation "
            f"is uncertain ({detail}). Run notesync auth login before retrying"
        ) from exc

    body_text = response.text or ""
    if response.status_code == 429:
        raise _KnownUnspentRefreshFailure(
            f"Granola rate-limited token refresh: {body_text[:300]}"
        )
    if response.status_code in (401, 403) or "invalid_grant" in body_text:
        raise DeviceSessionDead(
            f"CLI-owned refresh token was rejected ({response.status_code}); run notesync auth login"
        )
    if response.status_code != 200:
        if response.status_code >= 500:
            raise DeviceSessionPersistError(
                f"Granola returned {response.status_code} during refresh; rotation is uncertain. "
                "Run notesync auth login before retrying"
            )
        raise DeviceSessionDead(
            f"Granola rejected CLI-owned refresh ({response.status_code}): {body_text[:300]}"
        )

    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise DeviceSessionPersistError(
            "Granola returned an unreadable refresh response; rotation is uncertain. "
            "Run notesync auth login before retrying"
        ) from exc
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    if not access_token or not refresh_token:
        raise DeviceSessionPersistError(
            "Granola refresh omitted a rotated token; run notesync auth login before retrying"
        )
    obtained_at = time.time()
    jwt_expiry = jwt_expires_at(access_token)
    if jwt_expiry is not None:
        expiry = float(jwt_expiry)
    else:
        ttl = max(60, min(int(payload.get("expires_in") or 3600), 86400))
        expiry = obtained_at + ttl
    return DeviceSession(
        email=session.email,
        user_id=session.user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        obtained_at=obtained_at,
        expiry=expiry,
    )


def get_device_access_token(email: str) -> str:
    """Return a fresh token from the CLI-owned session for ``email``."""
    with _session_lock(email):
        path, session = _find_session(email)
        marker = _rotation_path(session.email)
        if marker.exists():
            raise DeviceSessionPersistError(
                f"an interrupted token rotation is recorded for {session.email}; "
                "run notesync auth login before retrying"
            )
        if not _session_is_expired(session):
            return session.access_token

        _atomic_write(
            marker,
            {
                "email": session.email,
                "started_at": time.time(),
                "session_obtained_at": session.obtained_at,
            },
        )
        try:
            refreshed = _refresh_device_session(session)
        except (_KnownUnspentRefreshFailure, DeviceSessionDead):
            marker.unlink(missing_ok=True)
            _fsync_directory(_session_dir())
            raise
        try:
            _atomic_write(path, refreshed.to_json_dict())
        except Exception as exc:
            raise DeviceSessionPersistError(
                "Granola rotated the CLI session but the new token could not be saved; "
                "run notesync auth login before retrying"
            ) from exc
        try:
            marker.unlink(missing_ok=True)
            _fsync_directory(_session_dir())
        except OSError as exc:
            raise DeviceSessionPersistError(
                "the rotated CLI session was saved but its safety marker could not be cleared; "
                "run notesync auth login before the next refresh"
            ) from exc
        return refreshed.access_token


def delete_device_sessions(email: Optional[str] = None) -> list[str]:
    """Remove CLI-owned sessions locally. This does not revoke them upstream."""
    sessions = list_device_sessions()
    targets = [
        session
        for session in sessions
        if email is None or _normalized_email(session.email) == _normalized_email(email)
    ]
    removed: list[str] = []
    for session in targets:
        with _session_lock(session.email):
            _session_path(session.email).unlink(missing_ok=True)
            _rotation_path(session.email).unlink(missing_ok=True)
            _fsync_directory(_session_dir())
        removed.append(session.email)
    return removed
