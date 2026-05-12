"""
Authentication module for NoteSync.
Reads access tokens from Granola's local configuration files.
Supports the legacy `supabase.json` layout and the newer multi-account
`stored-accounts.json` layout that ships with recent Granola builds.
"""

import json
import os
import platform
from pathlib import Path
from typing import Optional


class UserInfo:
    """User information from Granola"""

    def __init__(self, id: str, email: str, name: str, picture: Optional[str] = None):
        self.id = id
        self.email = email
        self.name = name
        self.picture = picture


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
        Picks the first account; multi-account selection can be added later if needed.
        """
        raw = json_data.get("accounts")
        if not raw:
            return None
        try:
            accounts = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(accounts, list) or not accounts:
                return None
            tokens_raw = accounts[0].get("tokens")
            if not tokens_raw:
                return None
            tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
            if isinstance(tokens, dict):
                return tokens.get("access_token")
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
        return None

    @staticmethod
    def get_access_token() -> str:
        """
        Get the access token from Granola's local configuration.

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

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    json_data = json.loads(f.read())
            except (OSError, json.JSONDecodeError):
                continue

            if not isinstance(json_data, dict):
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
