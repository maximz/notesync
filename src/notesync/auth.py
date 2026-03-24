"""
Authentication module for NoteSync.
Reads access tokens from Granola's local configuration file.
Uses parsing behavior compatible with current Granola local config formats.
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
        Get the platform-specific path to Granola configuration files.

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
        """Get the path to the Granola supabase.json file"""
        return GranolaAuth._get_config_path("supabase.json")

    @staticmethod
    def get_access_token() -> str:
        """
        Get the access token from Granola's local configuration.

        Returns:
            Access token string

        Raises:
            FileNotFoundError: If supabase.json doesn't exist
            ValueError: If no valid access token is found
        """
        file_path = GranolaAuth.get_supabase_config_path()

        # Check if file exists
        if not os.path.exists(file_path):
            raise FileNotFoundError(
                f"Granola configuration file not found at: {file_path}\n"
                "Make sure Granola is installed, running, and that you are logged in to the application."
            )

        # Read and parse the JSON file
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                file_content = f.read()
                json_data = json.loads(file_content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse Granola config file: {e}")

        access_token = None

        # Try WorkOS tokens first (updated auth method)
        if "workos_tokens" in json_data:
            try:
                workos_tokens = json_data["workos_tokens"]

                # Handle both string and object formats
                if isinstance(workos_tokens, str):
                    workos_tokens = json.loads(workos_tokens)
                elif isinstance(workos_tokens, dict):
                    pass  # Already a dict
                else:
                    workos_tokens = None

                if workos_tokens and "access_token" in workos_tokens:
                    access_token = workos_tokens["access_token"]
            except (json.JSONDecodeError, TypeError, KeyError):
                # Silently continue to Cognito fallback if WorkOS parsing fails
                pass

        # Fallback to Cognito tokens for backward compatibility
        if not access_token and "cognito_tokens" in json_data:
            try:
                cognito_tokens = json_data["cognito_tokens"]

                # Handle both string and object formats
                if isinstance(cognito_tokens, str):
                    cognito_tokens = json.loads(cognito_tokens)
                elif isinstance(cognito_tokens, dict):
                    pass  # Already a dict
                else:
                    cognito_tokens = None

                if cognito_tokens and "access_token" in cognito_tokens:
                    access_token = cognito_tokens["access_token"]
            except (json.JSONDecodeError, TypeError, KeyError):
                # Silently continue if Cognito parsing fails
                pass

        if not access_token:
            raise ValueError(
                "Access token not found in your local Granola data. "
                "Make sure Granola is installed, running, and that you are logged in to the application."
            )

        return access_token

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
