"""
Authentication module for NoteSync.
Reads access tokens from Granola's local configuration files.
Supports the legacy `supabase.json` layout and the newer multi-account
`stored-accounts.json` layout that ships with recent Granola builds.
"""

import base64
import json
import os
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from notesync import safestorage


class UserInfo:
    """User information from Granola"""

    def __init__(self, id: str, email: str, name: str, picture: Optional[str] = None):
        self.id = id
        self.email = email
        self.name = name
        self.picture = picture


@dataclass(frozen=True)
class GranolaAccount:
    """A single Granola account: an identity plus the credentials to act as it."""

    email: str
    access_token: str
    user_id: Optional[str] = None
    source: str = "stored-accounts"  # "stored-accounts" or "supabase"


def jwt_expires_at(access_token: str) -> Optional[int]:
    """
    Return the `exp` claim (unix seconds) from a JWT access token, or None
    if the token can't be decoded or has no `exp`. Signature is NOT verified —
    we're inspecting our own stored credentials only to decide whether to
    bother making an API call that will 401.
    """
    if not access_token or not isinstance(access_token, str):
        return None
    parts = access_token.split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1]
    # base64url, no padding — pad to a multiple of 4 before decoding.
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(padded)
        payload = json.loads(payload_bytes)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return int(exp)


def is_token_expired(access_token: str, *, now: Optional[int] = None, buffer_seconds: int = 60) -> bool:
    """
    True iff the JWT `exp` claim is in the past (with `buffer_seconds` of slack
    to avoid racing the API on near-expiry). Tokens we can't decode are treated
    as fresh — we'd rather attempt the call and surface a real 401 than skip
    accounts because of a parsing bug here.
    """
    exp = jwt_expires_at(access_token)
    if exp is None:
        return False
    if now is None:
        now = int(time.time())
    return exp - buffer_seconds <= now


class GranolaAuth:
    """
    Handles authentication with Granola by reading local configuration.
    """

    @staticmethod
    def _get_config_path(filename: str) -> str:
        """
        Get the platform-specific path to legacy Granola configuration files.

        Args:
            filename: The configuration filename (e.g., "supabase.json", "cache-v3.json")

        Returns:
            Full path to the configuration file
        """
        home_dir = Path.home()

        if platform.system() == "Windows":
            # Windows: %APPDATA%\Granola\{filename}
            return str(home_dir / "AppData" / "Roaming" / "Granola" / filename)
        else:
            # macOS and Linux: ~/Library/Application Support/Granola/{filename}
            return str(home_dir / "Library" / "Application Support" / "Granola" / filename)

    @staticmethod
    def get_supabase_config_path() -> str:
        """Get the path to the legacy Granola supabase.json file"""
        return GranolaAuth._get_config_path("supabase.json")

    @staticmethod
    def _get_stored_accounts_path() -> str:
        """
        Get the platform-specific path to the newer `stored-accounts.json`
        file. Linux deliberately uses `~/.config/Granola/` here even though
        the legacy `supabase.json` lookup keeps its older path — matches
        where Granola actually writes the new file.
        """
        home_dir = Path.home()
        system = platform.system()

        if system == "Windows":
            return str(home_dir / "AppData" / "Roaming" / "Granola" / "stored-accounts.json")
        if system == "Linux":
            return str(home_dir / ".config" / "Granola" / "stored-accounts.json")
        return str(home_dir / "Library" / "Application Support" / "Granola" / "stored-accounts.json")

    @staticmethod
    def _extract_workos(json_data: dict) -> Optional[str]:
        """Extract access_token from a `workos_tokens` field (supabase.json shape)."""
        raw = json_data.get("workos_tokens")
        if not raw:
            return None
        try:
            tokens = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(tokens, dict):
                return tokens.get("access_token")
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    @staticmethod
    def _extract_cognito(json_data: dict) -> Optional[str]:
        """Extract access_token from a `cognito_tokens` field (legacy supabase.json shape)."""
        raw = json_data.get("cognito_tokens")
        if not raw:
            return None
        try:
            tokens = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(tokens, dict):
                return tokens.get("access_token")
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    @staticmethod
    def _extract_stored_accounts(json_data: dict) -> Optional[str]:
        """
        Extract access_token from the multi-account `stored-accounts.json` shape:
        `{"accounts": "[{\"tokens\": \"{\\\"access_token\\\": ...}\", ...}]"}`.
        Both `accounts` and each `tokens` field are stringified JSON.
        Picks the first account; preserved for legacy single-token callers
        (`get_access_token`). Multi-account callers should use `list_accounts`.
        """
        accounts = GranolaAuth._parse_stored_accounts(json_data)
        if accounts:
            return accounts[0].access_token
        return None

    @staticmethod
    def _parse_stored_accounts(json_data: dict) -> List[GranolaAccount]:
        """Parse the `stored-accounts.json` body into one GranolaAccount per entry."""
        raw = json_data.get("accounts")
        if not raw:
            return []
        try:
            accounts_list = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(accounts_list, list):
            return []

        result: List[GranolaAccount] = []
        for account in accounts_list:
            if not isinstance(account, dict):
                continue
            email = account.get("email")
            user_id = account.get("userId") or account.get("user_id")
            tokens_raw = account.get("tokens")
            if not email or not tokens_raw:
                continue
            try:
                tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(tokens, dict):
                continue
            access_token = tokens.get("access_token")
            if not access_token:
                continue
            result.append(
                GranolaAccount(
                    email=email,
                    access_token=access_token,
                    user_id=user_id,
                    source="stored-accounts",
                )
            )
        return result

    @staticmethod
    def _parse_supabase_account(json_data: dict) -> Optional[GranolaAccount]:
        """Parse a single account out of the legacy `supabase.json` body."""
        access_token = (
            GranolaAuth._extract_workos(json_data)
            or GranolaAuth._extract_cognito(json_data)
        )
        if not access_token:
            return None

        email: Optional[str] = None
        user_id: Optional[str] = None
        user_info_raw = json_data.get("user_info")
        if user_info_raw is not None:
            try:
                user_info = (
                    json.loads(user_info_raw) if isinstance(user_info_raw, str) else user_info_raw
                )
                if isinstance(user_info, dict):
                    email = user_info.get("email")
                    user_id = user_info.get("id")
            except (json.JSONDecodeError, TypeError):
                pass
        if not email:
            return None
        return GranolaAccount(
            email=email, access_token=access_token, user_id=user_id, source="supabase"
        )

    @staticmethod
    def _read_json_dict(file_path: str) -> Optional[dict]:
        """Read a JSON file and return its top-level dict, or None on any failure."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    @staticmethod
    def _encrypted_path(plain_path: str) -> str:
        """The encrypted `.enc` sibling of a plaintext Granola config path."""
        return plain_path + ".enc"

    @staticmethod
    def _granola_config_exists(plain_path: str) -> bool:
        """True if the plaintext file or its encrypted `.enc` sibling exists."""
        return os.path.exists(plain_path) or os.path.exists(
            GranolaAuth._encrypted_path(plain_path)
        )

    @staticmethod
    def _read_granola_json(plain_path: str) -> Optional[dict]:
        """
        Read a Granola config file as a dict, preferring the encrypted `.enc`
        sibling when present.

        Modern Granola desktop writes only the encrypted files and leaves the
        old plaintext copies frozen (and therefore stale), so when both exist
        the `.enc` copy is authoritative. Falls back to the plaintext file for
        older builds, or when decryption is unavailable (non-macOS, missing
        Keychain entry not yet approved); the decrypt failure is surfaced on
        stderr so the cause isn't silently masked by a stale plaintext token.
        """
        enc_path = GranolaAuth._encrypted_path(plain_path)
        if os.path.exists(enc_path):
            if safestorage.is_supported():
                try:
                    return safestorage.load_encrypted_json(enc_path)
                except safestorage.SafeStorageError as e:
                    sys.stderr.write(f"notesync: could not read {enc_path}: {e}\n")
                    # Fall through to plaintext (present on transitional installs).
            elif not os.path.exists(plain_path):
                # Encrypted-only store on a platform we can't decrypt: say so,
                # otherwise the caller raises a misleading "not logged in" error.
                sys.stderr.write(
                    f"notesync: {enc_path} is encrypted and decryption is not "
                    "supported on this platform; no plaintext fallback found\n"
                )
        return GranolaAuth._read_json_dict(plain_path)

    @staticmethod
    def _merge_legacy_account(
        accounts: List[GranolaAccount], legacy: GranolaAccount
    ) -> List[GranolaAccount]:
        """
        Merge the legacy `supabase.json` account into the `stored-accounts.json`
        list, deduping by identity (user_id, else normalized email).

        Recent Granola builds keep only the *active* account's token fresh in
        `supabase.json` and stop rewriting that identity's entry in
        `stored-accounts.json`, so the stored copy can be expired while the
        legacy copy is current. When the same identity appears in both, keep
        whichever token is still valid — replacing an expired stored token with
        a fresh legacy one. With no identity match, append the legacy account.
        """

        def same_identity(existing: GranolaAccount) -> bool:
            if legacy.user_id and existing.user_id and existing.user_id == legacy.user_id:
                return True
            return existing.email.strip().lower() == legacy.email.strip().lower()

        # A legacy identity already present in stored-accounts is never appended
        # (that would duplicate it). Scan every entry -- not just the first match
        # -- so an unusual multi-entry layout can't leak a duplicate, and swap
        # the first expired match for the fresh legacy token. An undecodable
        # token counts as fresh, so dummy/non-JWT tokens keep the stored entry
        # (preserving prior dedupe behavior).
        matched = False
        swapped = False
        for i, existing in enumerate(accounts):
            if not same_identity(existing):
                continue
            matched = True
            if (
                not swapped
                and is_token_expired(existing.access_token)
                and not is_token_expired(legacy.access_token)
            ):
                accounts[i] = legacy
                swapped = True

        if not matched:
            accounts.append(legacy)
        return accounts

    @staticmethod
    def list_accounts() -> List[GranolaAccount]:
        """
        Return every Granola account NoteSync can authenticate as.

        Reads `stored-accounts.json` first (multi-account layout), then merges
        in the legacy `supabase.json` account. A distinct identity is appended;
        a duplicate identity is deduped (by user_id, else email), keeping the
        non-expired token so a fresh legacy copy can rescue an expired stored
        one. Order is preserved: stored-accounts entries come first in the order
        Granola wrote them.

        Raises:
            FileNotFoundError: If no candidate config file exists on disk.
            ValueError: If a config file exists but no recognizable account is found.
        """
        stored_path = GranolaAuth._get_stored_accounts_path()
        supabase_path = GranolaAuth.get_supabase_config_path()
        candidate_paths = [stored_path, supabase_path]

        any_file_existed = False
        accounts: List[GranolaAccount] = []

        if GranolaAuth._granola_config_exists(stored_path):
            any_file_existed = True
            data = GranolaAuth._read_granola_json(stored_path)
            if data is not None:
                accounts.extend(GranolaAuth._parse_stored_accounts(data))

        if GranolaAuth._granola_config_exists(supabase_path):
            any_file_existed = True
            data = GranolaAuth._read_granola_json(supabase_path)
            if data is not None:
                legacy = GranolaAuth._parse_supabase_account(data)
                if legacy is not None:
                    accounts = GranolaAuth._merge_legacy_account(accounts, legacy)

        if accounts:
            return accounts

        attempted = "\n  - ".join(candidate_paths)
        if not any_file_existed:
            raise FileNotFoundError(
                f"Granola configuration file not found at any of:\n  - {attempted}\n"
                "Make sure Granola is installed, running, and that you are logged in to the application."
            )
        raise ValueError(
            f"No Granola accounts found in your local data. Searched:\n  - {attempted}\n"
            "Make sure Granola is installed, running, and that you are logged in to the application."
        )

    @staticmethod
    def get_access_token() -> str:
        """
        Get an access token from Granola's local configuration.

        Backward-compatible single-account API. Returns the first available
        access token found across the candidate files; new multi-account
        callers should use `list_accounts()` instead.

        Tries `stored-accounts.json` first (newer multi-account layout), then
        `supabase.json` (legacy layout). When both files coexist on disk —
        which happens after a Granola app update leaves the old file behind —
        the legacy file's tokens are stale, so the newer file must win.

        Returns:
            Access token string

        Raises:
            FileNotFoundError: If no candidate config file exists on disk
            ValueError: If a config file exists but no recognizable token is found
        """
        candidate_paths = [
            GranolaAuth._get_stored_accounts_path(),
            GranolaAuth.get_supabase_config_path(),
        ]

        extractors = (
            GranolaAuth._extract_workos,
            GranolaAuth._extract_cognito,
            GranolaAuth._extract_stored_accounts,
        )

        any_file_existed = False
        for file_path in candidate_paths:
            if not GranolaAuth._granola_config_exists(file_path):
                continue
            any_file_existed = True

            json_data = GranolaAuth._read_granola_json(file_path)
            if json_data is None:
                continue

            for extract in extractors:
                access_token = extract(json_data)
                if access_token:
                    return access_token

        attempted = "\n  - ".join(candidate_paths)
        if not any_file_existed:
            raise FileNotFoundError(
                f"Granola configuration file not found at any of:\n  - {attempted}\n"
                "Make sure Granola is installed, running, and that you are logged in to the application."
            )
        raise ValueError(
            f"Access token not found in your local Granola data. Searched:\n  - {attempted}\n"
            "Make sure Granola is installed, running, and that you are logged in to the application."
        )

    @staticmethod
    def get_user_info() -> UserInfo:
        """
        Get user information from Granola's local configuration.

        Returns:
            UserInfo object with id, email, name, and optional picture

        Raises:
            FileNotFoundError: If supabase.json doesn't exist
            ValueError: If user info cannot be parsed
        """
        file_path = GranolaAuth.get_supabase_config_path()

        # Check if file exists (plaintext or encrypted sibling)
        if not GranolaAuth._granola_config_exists(file_path):
            raise FileNotFoundError(
                f"Granola configuration file not found at: {file_path}\n"
                "Make sure Granola is installed, running, and that you are logged in to the application."
            )

        try:
            # Read and parse the config (prefers the encrypted `.enc` sibling)
            json_data = GranolaAuth._read_granola_json(file_path)
            if json_data is None:
                raise ValueError("could not read Granola configuration")

            # Handle user_info which could be either a JSON string or an object
            user_info_data = json_data.get("user_info")

            if user_info_data is None:
                raise ValueError("user_info field not found in config")

            # Parse user_info (can be string or object)
            if isinstance(user_info_data, str):
                user_info = json.loads(user_info_data)
            elif isinstance(user_info_data, dict):
                user_info = user_info_data
            else:
                raise ValueError("user_info is neither a valid JSON string nor an object")

            # Extract user information
            user_id = user_info.get("id")
            email = user_info.get("email")

            # Name can be in user_metadata.name, name, or derived from email
            user_metadata = user_info.get("user_metadata", {})
            name = (
                user_metadata.get("name")
                or user_info.get("name")
                or (email.split("@")[0] if email else "Unknown")
            )

            # Picture is optional
            picture = user_metadata.get("picture")

            if not user_id:
                raise ValueError("User ID not found in user_info")

            if not email:
                raise ValueError("Email not found in user_info")

            return UserInfo(id=user_id, email=email, name=name, picture=picture)

        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse user_info: {e}")
        except Exception as e:
            raise ValueError(
                f"Failed to get Granola user info: {e}. "
                f"Please make sure Granola is installed, running, and that you are logged in to the application. "
                f"Attempted to read from: {file_path} (Platform: {platform.system()})"
            )
