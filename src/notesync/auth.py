"""
Authentication module for NoteSync.
Reads access tokens from Granola's local configuration files.
Supports the legacy `supabase.json` layout and the newer multi-account
`stored-accounts.json` layout that ships with recent Granola builds.
"""

import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


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
    def list_accounts() -> List[GranolaAccount]:
        """
        Return every Granola account NoteSync can authenticate as.

        Reads `stored-accounts.json` first (multi-account layout), then merges
        in the legacy `supabase.json` account if it isn't already represented
        (by user_id or email). Order is preserved: stored-accounts entries
        come first in the order Granola wrote them.

        Raises:
            FileNotFoundError: If no candidate config file exists on disk.
            ValueError: If a config file exists but no recognizable account is found.
        """
        stored_path = GranolaAuth._get_stored_accounts_path()
        supabase_path = GranolaAuth.get_supabase_config_path()
        candidate_paths = [stored_path, supabase_path]

        any_file_existed = False
        accounts: List[GranolaAccount] = []

        if os.path.exists(stored_path):
            any_file_existed = True
            data = GranolaAuth._read_json_dict(stored_path)
            if data is not None:
                accounts.extend(GranolaAuth._parse_stored_accounts(data))

        seen_user_ids = {a.user_id for a in accounts if a.user_id}
        # Normalize email for case/whitespace-insensitive dedupe. `User@x.com`
        # and `user@x.com` are the same identity to Granola; treat them so.
        seen_emails = {a.email.strip().lower() for a in accounts if a.email}

        if os.path.exists(supabase_path):
            any_file_existed = True
            data = GranolaAuth._read_json_dict(supabase_path)
            if data is not None:
                legacy = GranolaAuth._parse_supabase_account(data)
                if legacy is not None and not (
                    (legacy.user_id and legacy.user_id in seen_user_ids)
                    or legacy.email.strip().lower() in seen_emails
                ):
                    accounts.append(legacy)

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
            if not os.path.exists(file_path):
                continue
            any_file_existed = True

            json_data = GranolaAuth._read_json_dict(file_path)
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

        # Check if file exists
        if not os.path.exists(file_path):
            raise FileNotFoundError(
                f"Granola configuration file not found at: {file_path}\n"
                "Make sure Granola is installed, running, and that you are logged in to the application."
            )

        try:
            # Read and parse the JSON file
            with open(file_path, "r", encoding="utf-8") as f:
                file_content = f.read()
                json_data = json.loads(file_content)

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
