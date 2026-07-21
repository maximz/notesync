"""
Per-account token store and refresh logic for notesync.

Manages token persistence and refresh in ~/.cache/notesync/tokens/
so that notesync can mint fresh access tokens without re-reading
Granola's (potentially encrypted) credential files on every run.
"""

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import requests

from .api import API_CONFIG, get_user_agent
from .auth import is_token_expired, jwt_expires_at

if TYPE_CHECKING:
    from .auth import GranolaAccount


class TokenRefreshError(Exception):
    """Base error for a failed token refresh. Callers may catch this to cover both subtypes."""


class TokenRefreshDead(TokenRefreshError):
    """
    The refresh token is permanently rejected (HTTP 401/403, `invalid_grant`, or an
    empty/missing access_token in a 200 body). A human must re-bootstrap credentials
    (re-open Granola). Retrying with the same token will not help.
    """


class TokenRefreshTransient(TokenRefreshError):
    """
    A retryable failure (rate limiting after retries, network/connection error). The
    refresh token is presumed still valid; the next scheduled run should try again.
    """


class TokenPersistError(Exception):
    """
    Raised when a rotated token could NOT be written to the store after a successful
    refresh. This is high-severity: the endpoint has already SPENT the old refresh
    token and returned a rotated one that we failed to save. Re-presenting the spent
    token on the next run can revoke the whole token family. We deliberately fail loudly
    (no auto-delete, no re-bootstrap) so a human intervenes before the next run.
    """


def _account_subdir(email: str) -> str:
    """Map an account email to a filesystem-safe subdirectory name for token storage."""
    return re.sub(r"[^a-z0-9]+", "_", email.lower()).strip("_") or "account"


def _get_token_store_dir() -> Path:
    """Return the directory used for per-account token store files."""
    return Path(os.environ.get("NOTESYNC_TOKEN_DIR", Path.home() / ".cache" / "notesync" / "tokens"))


def _token_store_path(subdir: str) -> Path:
    return _get_token_store_dir() / f"{subdir}.json"


def _lockfile_path(subdir: str) -> Path:
    return _get_token_store_dir() / f"{subdir}.lock"


@contextmanager
def _account_lock(subdir: str):
    """Acquire an exclusive flock over the per-account lock file. Creates the token store dir first."""
    store_dir = _get_token_store_dir()
    store_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = _lockfile_path(subdir)
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _read_token_store(path: Path) -> Optional[dict]:
    """Read JSON from path, return None on any failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _write_token_store(path: Path, access_token: str, refresh_token: str, expiry: float) -> None:
    """Atomically write token store JSON with mode 0600."""
    payload = json.dumps(
        {"access_token": access_token, "refresh_token": refresh_token, "expiry": expiry}
    )
    tmp_path = path.parent / (path.name + ".tmp")
    try:
        # O_NOFOLLOW: refuse to write through a symlink planted at the temp path.
        fd = os.open(
            str(tmp_path),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def refresh_access_token(refresh_token: str) -> dict:
    """
    Call Granola's refresh endpoint to obtain a new access token.

    Returns a dict with keys ``access_token``, ``refresh_token``, ``expires_in``.
    Raises ``TokenRefreshDead`` when the refresh token is rejected (401/403,
    ``invalid_grant``, or an empty/missing access_token) — a human must re-bootstrap.
    Raises ``TokenRefreshTransient`` on retryable failures (429-after-retries, network
    error) — the next run should try again.

    The refresh_token in the response may be rotated; if the response omits it,
    the caller's current refresh_token is preserved.
    """
    url = f"{API_CONFIG['API_URL']}/refresh-access-token"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": get_user_agent(),
        "X-Client-Version": API_CONFIG["CLIENT_VERSION"],
        "X-Granola-Platform": "macos",
    }
    body = {"refresh_token": refresh_token}

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url,
                json=body,
                headers=headers,
                timeout=(10, 60),
            )
        except requests.RequestException as exc:
            raise TokenRefreshTransient(
                f"network error calling refresh endpoint: {exc}"
            ) from exc

        if response.status_code == 429:
            if attempt < max_retries:
                try:
                    wait = min(float(response.headers.get("Retry-After", "1")), 30.0)
                except (ValueError, TypeError):
                    wait = 1.0
                time.sleep(wait)
                continue
            # Exhausted retries on 429 — retryable, not a dead token.
            raise TokenRefreshTransient(
                f"refresh endpoint returned 429 after {max_retries} retries; body: {response.text[:500]}"
            )

        # A rejected refresh token (auth failure) is dead: retrying won't help,
        # and some deployments surface `invalid_grant` with a non-401 status.
        body_text = response.text or ""
        if response.status_code in (401, 403) or "invalid_grant" in body_text:
            raise TokenRefreshDead(
                f"refresh token rejected: status {response.status_code}; body: {body_text[:500]}"
            )

        if response.status_code != 200:
            # Unclassified non-200 (e.g. 5xx): treat as transient so a blip retries.
            raise TokenRefreshTransient(
                f"refresh endpoint returned {response.status_code}; body: {body_text[:500]}"
            )

        # 200 path
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise TokenRefreshTransient(
                f"refresh endpoint returned non-JSON 200 body: {exc}"
            ) from exc

        new_access_token = data.get("access_token")
        if not new_access_token:
            # A 200 with no usable token means this refresh path is dead.
            raise TokenRefreshDead(
                f"refresh endpoint returned 200 but access_token is missing or empty; got: {data!r}"
            )

        return {
            "access_token": new_access_token,
            "refresh_token": data.get("refresh_token") or refresh_token,
            "expires_in": data.get("expires_in", 3600),
        }

    # Should not reach here, but satisfy the type-checker
    raise TokenRefreshTransient("refresh_access_token: exhausted retry loop unexpectedly")


def get_access_token_for(account: "GranolaAccount") -> str:
    """
    Return a fresh access token for the given account.

    On first call (no store file): bootstraps from ``account.refresh_token``.
    On subsequent calls: reads the store; returns cached token if fresh, refreshes if stale.
    The refresh_token is persisted and rotated in the store after every refresh so
    each refresh_token value is only presented to the endpoint once.

    Raises ``TokenRefreshError`` if no refresh path is available or the endpoint fails.
    """
    subdir = _account_subdir(account.email)
    store_path = _token_store_path(subdir)

    with _account_lock(subdir):
        store = _read_token_store(store_path)

        if store is not None:
            cached_access = store.get("access_token", "")
            cached_expiry = store.get("expiry", 0.0)

            if cached_access and not _is_store_expired(cached_access, cached_expiry):
                # Fresh — return without refresh
                return cached_access

            # Stale — refresh. Prefer the stored (rotated) refresh token; if the
            # store somehow holds an empty one, fall back to the account's
            # plaintext refresh token so we're not permanently wedged.
            stored_rt = store.get("refresh_token") or account.refresh_token
            if not stored_rt:
                raise TokenRefreshDead(
                    f"no refresh token in store for {account.email}; re-open Granola to refresh"
                )
            result = refresh_access_token(stored_rt)
            _persist(store_path, result)
            return result["access_token"]

        # Bootstrap: no store file yet.
        # If the account's on-disk access_token is still fresh, use it directly
        # without hitting the refresh endpoint. This covers the common path where
        # Granola has recently written a valid token and we haven't yet seeded
        # our own store.
        if account.access_token and not is_token_expired(account.access_token):
            # Only seed the store when we have a real refresh token to persist.
            # Writing an empty refresh_token would wedge the account once the
            # access token later goes stale; skip persisting so a later run can
            # re-bootstrap once a refresh token is present.
            if account.refresh_token:
                exp = jwt_expires_at(account.access_token)
                if exp is not None:
                    expiry = float(exp)
                else:
                    expiry = time.time() + 3600
                _write_token_store(
                    store_path,
                    access_token=account.access_token,
                    refresh_token=account.refresh_token,
                    expiry=expiry,
                )
            return account.access_token

        # On-disk token is stale (or absent) and we have a refresh token —
        # bootstrap via the refresh endpoint.
        if not account.refresh_token:
            raise TokenRefreshDead(
                f"no refresh token available for {account.email}; "
                "re-open Granola to refresh credentials"
            )
        result = refresh_access_token(account.refresh_token)
        _persist(store_path, result)
        return result["access_token"]


def _is_store_expired(access_token: str, stored_expiry: float) -> bool:
    """True if the token should be considered stale (need a refresh)."""
    # Use JWT exp if available; fall back to the stored numeric expiry.
    exp = jwt_expires_at(access_token)
    if exp is not None:
        return is_token_expired(access_token)
    # Non-JWT token: rely on the stored expiry timestamp.
    return time.time() >= stored_expiry - 60


def _persist(store_path: Path, result: dict) -> None:
    """
    Compute expiry and write the rotated token to the store.

    The preceding refresh already SPENT the old refresh token, so losing the
    rotated one here is dangerous: the next run would replay the spent token and
    can revoke the whole token family. Retry the write, and if it still fails,
    raise ``TokenPersistError`` (a loud, distinct failure) rather than swallowing
    it. We intentionally do NOT delete or re-bootstrap the store on failure —
    re-presenting any spent token is unsafe — so a human must intervene.
    """
    access_token = result["access_token"]
    exp = jwt_expires_at(access_token)
    if exp is not None:
        expiry = float(exp)
    else:
        # Clamp the server-provided TTL to a sane range before trusting it.
        expires_in = max(60, min(int(result.get("expires_in", 3600)), 86400))
        expiry = time.time() + expires_in

    last_exc: Optional[Exception] = None
    for _ in range(3):  # 1 initial attempt + 2 retries
        try:
            _write_token_store(
                store_path,
                access_token=access_token,
                refresh_token=result["refresh_token"],
                expiry=expiry,
            )
            return
        except Exception as exc:  # noqa: BLE001 — any write failure is in scope
            last_exc = exc

    raise TokenPersistError(
        f"CRITICAL: refreshed token but could not persist it to {store_path}. "
        "The old refresh token has already been spent; the rotated token is now "
        "lost. Do NOT re-run until the store is repaired, or the token family may "
        f"be revoked (logging you out of Granola). Last write error: {last_exc}"
    ) from last_exc
