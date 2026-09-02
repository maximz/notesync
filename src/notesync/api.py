"""
Granola API client.
Implements Granola API behaviors compatible with the Granola extension for Raycast.
"""

import json
import platform
import plistlib
import threading
import time
from pathlib import Path
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

# Fallbacks used off macOS, when Granola isn't installed, or if a plist can't
# be read. Bump occasionally to stay recent.
_FALLBACK_CLIENT_VERSION = "7.427.8"
_FALLBACK_ELECTRON_VERSION = "42.4.1"
_GRANOLA_INFO_PLIST = Path("/Applications/Granola.app/Contents/Info.plist")
_ELECTRON_INFO_PLIST = Path(
    "/Applications/Granola.app/Contents/Frameworks/"
    "Electron Framework.framework/Resources/Info.plist"
)


def _read_plist_string(plist_path: Path, key: str) -> Optional[str]:
    """
    Best-effort read of a string value from a macOS plist. Pure stdlib,
    read-only, no subprocess/network. Returns None off macOS, when the file
    is absent, or on any read/parse error. Never raises.
    """
    if platform.system() != "Darwin":
        return None
    try:
        with open(plist_path, "rb") as f:
            info = plistlib.load(f)
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    return None


def _detect_client_version() -> str:
    """
    The installed Granola desktop version, so requests and the token-refresh
    call advertise a current, unremarkable client version (this is the value
    the API keys on) rather than a stale hardcoded one.
    """
    return _read_plist_string(_GRANOLA_INFO_PLIST, "CFBundleShortVersionString") or _FALLBACK_CLIENT_VERSION


def _detect_electron_version() -> str:
    """
    The installed Electron framework version, for the User-Agent's Electron
    token. (Chrome is left hardcoded: it has no clean static source and is
    low-signal.)
    """
    return _read_plist_string(_ELECTRON_INFO_PLIST, "CFBundleVersion") or _FALLBACK_ELECTRON_VERSION


API_CONFIG = {
    "API_URL": "https://api.granola.ai/v1",
    "API_URL_V2": "https://api.granola.ai/v2",
    "STREAM_API_URL": "https://stream.api.granola.ai/v1",
    # Auto-detected from the installed Granola build so requests (and the
    # token-refresh call) present the current client version; falls back to a
    # recent known value when detection isn't possible.
    "CLIENT_VERSION": _detect_client_version(),
    "ELECTRON_VERSION": _detect_electron_version(),
}

# (connect, read) timeout in seconds for every request. Without this, a stalled
# socket read hangs the sync forever -- the failure mode that froze the
# scheduled granola sync for days. Bounds each attempt so retries/backoff and
# the outer cron timeout can do their job.
DEFAULT_REQUEST_TIMEOUT = (10, 60)
DEFAULT_INTERNAL_API_RATE = 2.0
MIN_INTERNAL_API_RATE = 0.25


def get_user_agent() -> str:
    """
    Get the User-Agent string to mimic the Granola desktop app.
    """
    version = API_CONFIG["CLIENT_VERSION"]
    electron = API_CONFIG["ELECTRON_VERSION"]
    return (
        f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Granola/{version} Chrome/148.0.7778.265 "
        f"Electron/{electron} Safari/537.36"
    )


# ============================================================================
# Granola API Client
# ============================================================================


class AdaptiveRateLimiter:
    """Conservative per-client pacing with automatic slowdown after HTTP 429."""

    def __init__(self, rate: float = DEFAULT_INTERNAL_API_RATE):
        self._rate = max(MIN_INTERNAL_API_RATE, float(rate))
        self._next_request_at = 0.0
        self._lock = threading.Lock()

    @property
    def rate(self) -> float:
        with self._lock:
            return self._rate

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request_at - now)
            slot = max(now, self._next_request_at)
            self._next_request_at = slot + (1.0 / self._rate)
        if delay > 0:
            time.sleep(delay)

    def on_rate_limit(self) -> None:
        with self._lock:
            self._rate = max(MIN_INTERNAL_API_RATE, self._rate / 2.0)


def _retry_after_seconds(response: requests.Response) -> float:
    try:
        return max(0.0, min(float(response.headers.get("Retry-After", "0")), 60.0))
    except (TypeError, ValueError):
        return 0.0


class GranolaAPI:
    """
    API client for Granola.
    Uses behavior-compatible request and parsing logic.
    """

    def __init__(
        self,
        access_token: str,
        rate_limit: float = DEFAULT_INTERNAL_API_RATE,
    ):
        """
        Initialize the API client.

        The token must be supplied explicitly so multi-account callers can't
        accidentally drop their per-account context. Use
        `GranolaAuth.list_accounts()` to discover which token to pass.

        Args:
            access_token: Bearer token to use against api.granola.ai.
        """
        if not access_token:
            raise ValueError(
                "GranolaAPI requires an explicit access_token. "
                "Use GranolaAuth.list_accounts() to enumerate available accounts."
            )
        self.access_token = access_token
        self.session = requests.Session()
        self.rate_limiter = AdaptiveRateLimiter(rate_limit)
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
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": get_user_agent(),
            "X-Client-Version": API_CONFIG["CLIENT_VERSION"],
            "X-Granola-Platform": "darwin",
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

        # Bound every attempt so a stalled connection can't hang forever.
        # Callers may still override per-request.
        kwargs.setdefault("timeout", DEFAULT_REQUEST_TIMEOUT)

        for attempt in range(max_retries):
            try:
                self.rate_limiter.wait()
                response = self.session.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                last_exception = e

                # Don't retry on client errors (4xx) except 429 (rate limit)
                response = getattr(e, "response", None)
                if response is not None:
                    if 400 <= response.status_code < 500 and response.status_code != 429:
                        raise
                    if response.status_code == 429:
                        self.rate_limiter.on_rate_limit()

                # Exponential backoff, while honoring a bounded Retry-After.
                if attempt < max_retries - 1:
                    wait_time = float(2**attempt)
                    if response is not None and response.status_code == 429:
                        wait_time = max(wait_time, _retry_after_seconds(response))
                    time.sleep(wait_time)

        # All retries failed
        raise last_exception

    # ========================================================================
    # API Methods (matching fetchData.ts and granolaApi.ts)
    # ========================================================================

    def get_documents(self) -> GetDocumentsResponse:
        """
        Fetch all documents (notes) from Granola.

        POST /v2/get-documents

        Returns:
            GetDocumentsResponse with docs and deleted lists

        Raises:
            requests.HTTPError: If the API request fails
        """
        url = f"{API_CONFIG['API_URL_V2']}/get-documents"

        try:
            response = self._retry_request("POST", url)
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
