"""
Granola API client.
Implements Granola API behaviors compatible with the Granola extension for Raycast.
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

import requests

from .auth import GranolaAuth
from .models import (
    Document,
    Folder,
    GetDocumentsResponse,
    FoldersResponse,
    TranscriptSegment,
    PanelContent,
    GranolaCache,
)


# ============================================================================
# API configuration aligned with the observed Granola desktop client behavior.
# ============================================================================

API_CONFIG = {
    "API_URL": "https://api.granola.ai/v1",
    "API_URL_V2": "https://api.granola.ai/v2",
    "STREAM_API_URL": "https://stream.api.granola.ai/v1",
    "CLIENT_VERSION": "6.72.0",
}


def get_user_agent() -> str:
    """
    Get the User-Agent string to mimic the Granola desktop app.
    """
    version = API_CONFIG["CLIENT_VERSION"]
    return (
        f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Granola/{version} Chrome/136.0.7103.115 "
        f"Electron/36.3.2 Safari/537.36"
    )


# ============================================================================
# Granola API Client
# ============================================================================


class GranolaAPI:
    """
    API client for Granola.
    Uses behavior-compatible request and parsing logic.
    """

    def __init__(self, access_token: Optional[str] = None):
        """
        Initialize the API client.

        Args:
            access_token: Optional access token. If not provided, will be read from config.
        """
        self.access_token = access_token or GranolaAuth.get_access_token()
        self.session = requests.Session()
        self._setup_session()

    def _setup_session(self):
        """Configure the requests session with default headers"""
        self.session.headers.update(self._get_headers())

    def _get_headers(self, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Create HTTP headers for API requests.

        Args:
            extra_headers: Optional additional headers to include

        Returns:
            Dictionary of HTTP headers
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "User-Agent": get_user_agent(),
            "X-Client-Version": API_CONFIG["CLIENT_VERSION"],
        }

        if extra_headers:
            headers.update(extra_headers)

        return headers

    def _handle_response(self, response: requests.Response, operation_name: str) -> Any:
        """
        Handle API response and errors.

        Args:
            response: The HTTP response object
            operation_name: Name of the operation for error messages

        Returns:
            Parsed JSON response

        Raises:
            requests.HTTPError: If the request failed
        """
        if not response.ok:
            error_message = f"{operation_name} failed: {response.status_code} {response.reason}"

            try:
                error_body = response.text
                if error_body:
                    try:
                        error_json = response.json()
                        if "error" in error_json:
                            error_message = f"{operation_name} failed: {error_json['error']}"
                        elif "message" in error_json:
                            error_message = f"{operation_name} failed: {error_json['message']}"
                        else:
                            error_message = f"{operation_name} failed: {error_body}"
                    except json.JSONDecodeError:
                        error_message = f"{operation_name} failed: {error_body}"
            except Exception:
                pass

            raise requests.HTTPError(error_message, response=response)

        return response.json()

    def _retry_request(
        self,
        method: str,
        url: str,
        max_retries: int = 3,
        **kwargs,
    ) -> requests.Response:
        """
        Make an HTTP request with exponential backoff retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            max_retries: Maximum number of retry attempts
            **kwargs: Additional arguments to pass to requests

        Returns:
            HTTP response

        Raises:
            requests.RequestException: If all retries fail
        """
        last_exception = None

        for attempt in range(max_retries):
            try:
                response = self.session.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                last_exception = e

                # Don't retry on client errors (4xx) except 429 (rate limit)
                if hasattr(e, "response") and e.response is not None:
                    if 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                        raise

                # Exponential backoff: 1s, 2s, 4s
                if attempt < max_retries - 1:
                    wait_time = 2**attempt
                    time.sleep(wait_time)

        # All retries failed
        raise last_exception

    # ========================================================================
    # API Methods (matching fetchData.ts and granolaApi.ts)
    # ========================================================================

    def get_documents(self) -> GetDocumentsResponse:
        """
        Fetch all documents (notes) from Granola.

        GET /v2/get-documents

        Returns:
            GetDocumentsResponse with docs and deleted lists

        Raises:
            requests.HTTPError: If the API request fails
        """
        url = f"{API_CONFIG['API_URL_V2']}/get-documents"

        try:
            response = self._retry_request("GET", url)
            data = self._handle_response(response, "Get documents")
            return GetDocumentsResponse(**data)
        except Exception as e:
            raise Exception(f"Failed to fetch documents: {e}")

    def get_transcript(self, document_id: str) -> List[TranscriptSegment]:
        """
        Fetch transcript segments for a document.

        POST /v1/get-document-transcript
        Body: {"document_id": str}

        Args:
            document_id: The document ID to fetch transcript for

        Returns:
            List of TranscriptSegment objects

        Raises:
            requests.HTTPError: If the API request fails
        """
        url = f"{API_CONFIG['API_URL']}/get-document-transcript"
        body = {"document_id": document_id}

        try:
            response = self._retry_request("POST", url, json=body)
            data = self._handle_response(response, "Get transcript")

            # Parse segments
            segments = [TranscriptSegment(**segment) for segment in data]
            return segments

        except Exception as e:
            raise Exception(f"Failed to fetch transcript for document {document_id}: {e}")

    def get_folders(self) -> FoldersResponse:
        """
        Fetch all folders (document lists) from Granola.

        POST /v1/get-document-lists-metadata
        Body: {"include_document_ids": true, "include_only_joined_lists": false}

        Returns:
            FoldersResponse with lists dictionary

        Raises:
            requests.HTTPError: If the API request fails
        """
        url = f"{API_CONFIG['API_URL']}/get-document-lists-metadata"
        body = {
            "include_document_ids": True,
            "include_only_joined_lists": False,
        }

        try:
            response = self._retry_request("POST", url, json=body)
            data = self._handle_response(response, "Get folders")
            return FoldersResponse(**data)

        except Exception as e:
            raise Exception(f"Failed to fetch folders: {e}")

    # ========================================================================
    # Cache Reader (for AI-generated panels)
    # ========================================================================

    def read_cache(self) -> Optional[GranolaCache]:
        """
        Read Granola's local cache file (cache-v3.json).
        Contains AI-generated panel content not available via API.

        Returns:
            GranolaCache object or None if cache doesn't exist

        Raises:
            Exception: If cache file exists but cannot be parsed
        """
        cache_path = GranolaAuth.get_cache_config_path()

        if not os.path.exists(cache_path):
            return None

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                content = f.read()
                cache_data = json.loads(content)

            # Handle both string and object formats (like getCache.ts)
            cache_value = cache_data.get("cache")
            if isinstance(cache_value, str):
                cache_value = json.loads(cache_value)

            return GranolaCache(**cache_value)

        except Exception as e:
            raise Exception(f"Failed to read cache from {cache_path}: {e}")

    def get_document_panels(self, document_id: str, verbose: bool = False) -> Dict[str, PanelContent]:
        """
        Get AI-generated panels for a specific document from cache.
        Panels include summaries, action items, and other enhanced content.

        Args:
            document_id: The document ID to get panels for
            verbose: If True, print debug information

        Returns:
            Dictionary mapping panel_id to PanelContent
            Returns empty dict if cache doesn't exist or document has no panels
        """
        try:
            cache = self.read_cache()
        except Exception as e:
            if verbose:
                print(f"DEBUG: Failed to read cache: {e}")
            return {}

        if not cache:
            if verbose:
                print("DEBUG: Cache is None")
            return {}

        if not cache.state:
            if verbose:
                print("DEBUG: Cache has no state")
            return {}

        if not cache.state.documentPanels:
            if verbose:
                print("DEBUG: Cache state has no documentPanels")
            return {}

        doc_panels = cache.state.documentPanels.get(document_id, {})

        if verbose:
            print(f"DEBUG: Found {len(doc_panels)} panels for document {document_id[:8]}")
            if doc_panels:
                print(f"DEBUG: Panel IDs: {list(doc_panels.keys())}")

        # Convert raw dict to PanelContent objects
        result = {}
        for panel_id, panel_data in doc_panels.items():
            try:
                # Ensure panel_data is a dict
                if not isinstance(panel_data, dict):
                    if verbose:
                        print(f"DEBUG: Panel {panel_id} data is not a dict: {type(panel_data)}")
                    continue

                # Panel data should have 'original_content' and/or 'content'
                panel = PanelContent(**panel_data)
                result[panel_id] = panel

                if verbose:
                    has_content = "content" if panel.content else "no content"
                    has_html = "HTML" if panel.original_content else "no HTML"
                    print(f"DEBUG: Panel {panel_id}: {has_content}, {has_html}")

            except Exception as e:
                if verbose:
                    print(f"DEBUG: Failed to parse panel {panel_id}: {e}")
                    print(f"DEBUG: Panel data keys: {panel_data.keys() if isinstance(panel_data, dict) else 'not a dict'}")
                continue

        return result
