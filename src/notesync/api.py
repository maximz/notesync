"""
Granola API client.
Implements Granola API behaviors compatible with the Granola extension for Raycast.
"""

import json
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
    # Panel Methods (AI-generated content)
    # ========================================================================

    def get_document_panels(self, document_id: str, verbose: bool = False) -> Dict[str, PanelContent]:
        """
        Get AI-generated panels for a specific document via API.

        POST /v1/get-document-panels
        Body: {"document_id": str}

        Args:
            document_id: The document ID to get panels for
            verbose: If True, print debug information

        Returns:
            Dictionary mapping panel_id to PanelContent
            Returns empty dict if the document has no panels
        """
        url = f"{API_CONFIG['API_URL']}/get-document-panels"
        body = {"document_id": document_id}

        try:
            response = self._retry_request("POST", url, json=body)
            data = self._handle_response(response, "Get document panels")
        except Exception as e:
            if verbose:
                print(f"DEBUG: Failed to fetch panels for {document_id[:8]}: {e}")
            return {}

        if not isinstance(data, list):
            if verbose:
                print(f"DEBUG: Unexpected response type: {type(data)}")
            return {}

        if verbose:
            print(f"DEBUG: Found {len(data)} panels for document {document_id[:8]}")

        result = {}
        for panel_data in data:
            try:
                panel_id = panel_data.get("id", "")
                if not panel_id:
                    continue
                panel = PanelContent(**panel_data)
                result[panel_id] = panel

                if verbose:
                    has_content = "content" if panel.content else "no content"
                    has_html = "HTML" if panel.original_content else "no HTML"
                    print(f"DEBUG: Panel {panel_id[:8]}: {has_content}, {has_html}")
            except Exception as e:
                if verbose:
                    print(f"DEBUG: Failed to parse panel: {e}")
                continue

        return result
