"""
Granola API client.
Implements Granola API behaviors compatible with the Granola extension for Raycast.
"""

import json
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import markdown as markdown_lib
import requests

from .auth import GranolaAuth, UserInfo
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


def format_transcript_for_prompt(segments: List[TranscriptSegment]) -> str:
    """
    Format transcript segments into the text format expected by the Granola LLM proxy.
    Matches the desktop client's p_() function: "Me: text Them: text ..." with speaker labels.

    Args:
        segments: List of transcript segments

    Returns:
        Formatted transcript string
    """
    if not segments:
        return ""

    sorted_segments = sorted(segments, key=lambda s: s.get_start_time())

    result = ""
    last_speaker = None

    for seg in sorted_segments:
        speaker = "Me" if seg.source == "microphone" else "Them"
        if speaker != last_speaker:
            result += " " + speaker + ": "
            last_speaker = speaker
        result += seg.text + " "

    return result.strip()


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

    # ========================================================================
    # Note Generation Methods
    # ========================================================================

    def create_panel(self, document_id: str, template_slug: str = "meeting-summary-consolidated", verbose: bool = False) -> str:
        """
        Create a new panel for a document.

        POST /v1/create-document-panel
        Body: full panel object

        Args:
            document_id: The document ID
            template_slug: The template slug to use
            verbose: If True, print debug information

        Returns:
            The panel ID
        """
        now = datetime.utcnow().isoformat() + "Z"
        panel_id = str(uuid.uuid4())

        panel_data = {
            "id": panel_id,
            "document_id": document_id,
            "title": "Summary",
            "content": None,
            "template_slug": template_slug,
            "created_at": now,
            "updated_at": now,
            "content_updated_at": None,
            "deleted_at": None,
            "last_viewed_at": now,
            "affinity_note_id": None,
            "original_content": None,
            "suggested_questions": None,
            "generated_lines": None,
            "user_feedback": None,
            "ydoc_version": None,
        }

        url = f"{API_CONFIG['API_URL']}/create-document-panel"

        if verbose:
            print(f"DEBUG: Creating panel {panel_id[:8]} for document {document_id[:8]}")

        try:
            response = self._retry_request("POST", url, json=panel_data)
            self._handle_response(response, "Create panel")
        except Exception as e:
            raise Exception(f"Failed to create panel for document {document_id}: {e}")

        if verbose:
            print(f"DEBUG: Panel {panel_id[:8]} created successfully")

        return panel_id

    def update_panel(self, panel_id: str, document_id: str, content_html: str, verbose: bool = False) -> None:
        """
        Update a panel with generated content.

        POST /v1/update-document-panel
        Body: panel update object (must include document_id)

        Args:
            panel_id: The panel ID to update
            document_id: The document ID the panel belongs to
            content_html: The HTML content to set
            verbose: If True, print debug information
        """
        now = datetime.utcnow().isoformat() + "Z"

        update_data = {
            "id": panel_id,
            "document_id": document_id,
            "content": content_html,
            "original_content": content_html,
            "last_viewed_at": now,
            "content_updated_at": now,
        }

        url = f"{API_CONFIG['API_URL']}/update-document-panel"

        if verbose:
            print(f"DEBUG: Updating panel {panel_id[:8]} with {len(content_html)} chars of content")

        try:
            response = self._retry_request("POST", url, json=update_data)
            self._handle_response(response, "Update panel")
        except Exception as e:
            raise Exception(f"Failed to update panel {panel_id}: {e}")

        if verbose:
            print(f"DEBUG: Panel {panel_id[:8]} updated successfully")

    def stream_generate_notes(
        self,
        document: Document,
        transcript_segments: List[TranscriptSegment],
        user_info: Optional[UserInfo] = None,
        verbose: bool = False,
    ) -> str:
        """
        Stream-generate notes via the LLM proxy endpoint.

        POST https://stream.api.granola.ai/v1/llm-proxy-stream
        Body: {prompt_slug, prompt_variables, chat_history}

        Args:
            document: The document to generate notes for
            transcript_segments: Transcript segments for the document
            user_info: Optional user info for my_name field
            verbose: If True, print debug information

        Returns:
            The generated content as a string (markdown/HTML from the LLM)
        """
        transcript_text = format_transcript_for_prompt(transcript_segments)

        if verbose:
            print(f"DEBUG: Transcript length: {len(transcript_text)} chars")

        # Build prompt variables matching the Granola desktop client
        my_name = user_info.name if user_info else ""
        notes_text = document.notes_markdown or document.notes_plain or ""

        # Build participants string
        participants = ""
        if document.people:
            parts = []
            creator = document.people.creator
            if creator:
                parts.append(f"{creator.name} <{creator.email}>")
            for att in document.people.attendees or []:
                name = att.name or (att.email.split("@")[0].title() if att.email else "")
                if att.email:
                    parts.append(f"{name} <{att.email}>")
                elif name:
                    parts.append(name)
            participants = ", ".join(parts)

        # Meeting time/date from calendar event
        cal = document.google_calendar_event
        meeting_time = ""
        meeting_date = ""
        if cal and cal.get("start", {}).get("dateTime"):
            try:
                start_dt = datetime.fromisoformat(cal["start"]["dateTime"].replace("Z", "+00:00"))
                meeting_time = start_dt.strftime("%I:%M %p").lstrip("0")
                meeting_date = start_dt.strftime("%A, %B %d, %Y")
            except (ValueError, TypeError):
                pass

        todays_date = datetime.utcnow().strftime("%A, %B %d, %Y")
        calendar_title = ""
        if cal:
            calendar_title = cal.get("summary", "") or document.title or ""
        else:
            calendar_title = document.title or ""

        prompt_variables = {
            "transcript": transcript_text,
            "notes": notes_text,
            "my_name": my_name,
            "participants": participants,
            "calendar_event_title": calendar_title,
            "document_id": document.id,
            "time": meeting_time,
            "date": meeting_date,
            "todays_date": todays_date,
            "is_multi_language": False,
            "english_only_summary": True,
            "is_short_transcript": len(transcript_text) < 200,
            "has_long_user_notes": len(notes_text.split("\n")) >= 20 if notes_text else False,
        }

        body = {
            "prompt_slug": "meeting-summary-consolidated",
            "prompt_variables": prompt_variables,
            "chat_history": [],
        }

        url = f"{API_CONFIG['STREAM_API_URL']}/llm-proxy-stream"

        if verbose:
            print(f"DEBUG: Streaming generation from {url}")
            print(f"DEBUG: prompt_slug=meeting-summary-consolidated, transcript={len(transcript_text)} chars")

        try:
            response = self.session.post(url, json=body, stream=True, timeout=120)
            if not response.ok:
                error_text = response.text
                raise requests.HTTPError(
                    f"LLM proxy stream failed: {response.status_code} {response.reason}: {error_text}",
                    response=response,
                )
        except requests.RequestException as e:
            raise Exception(f"Failed to stream generate notes: {e}")

        # Parse streaming response: chunks separated by -----CHUNK_BOUNDARY-----
        # Each chunk is a JSON object in OpenAI-compatible format
        CHUNK_BOUNDARY = "-----CHUNK_BOUNDARY-----"
        raw_buffer = ""
        content = ""

        for raw_chunk in response.iter_content(chunk_size=4096):
            raw_buffer += raw_chunk.decode("utf-8", errors="replace")

        for part in raw_buffer.split(CHUNK_BOUNDARY):
            part = part.strip()
            if not part:
                continue
            try:
                chunk = json.loads(part)
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        content += text
            except json.JSONDecodeError:
                continue

        # Strip <notes>...</notes> wrapper if present (Granola LLM wraps content in these tags)
        content = re.sub(r"^\s*<notes>\s*", "", content)
        content = re.sub(r"\s*</notes>\s*$", "", content)
        content = content.strip()

        if verbose:
            print(f"DEBUG: Generated {len(content)} chars of content")

        return content

    def generate_notes_for_document(
        self,
        document: Document,
        transcript_segments: List[TranscriptSegment],
        user_info: Optional[UserInfo] = None,
        verbose: bool = False,
    ) -> str:
        """
        Full generation flow: create panel, stream content, update panel.

        Args:
            document: The document to generate notes for
            transcript_segments: Transcript segments
            user_info: Optional user info
            verbose: If True, print debug information

        Returns:
            The panel ID of the created panel
        """
        # Step 1: Create panel
        panel_id = self.create_panel(document.id, verbose=verbose)

        # Step 2: Stream-generate content
        try:
            generated_content = self.stream_generate_notes(
                document, transcript_segments, user_info=user_info, verbose=verbose
            )
        except Exception as e:
            if verbose:
                print(f"DEBUG: Generation failed, panel {panel_id[:8]} was created but has no content")
            raise

        if not generated_content.strip():
            if verbose:
                print(f"DEBUG: Generation returned empty content")
            raise Exception("Generation returned empty content")

        # Step 3: Update panel with generated content
        # The desktop client converts markdown to HTML before saving.
        # The LLM returns markdown; wrap it minimally so the panel API accepts it.
        # Granola stores panel content as HTML internally.
        content_html = markdown_lib.markdown(
            generated_content,
            extensions=["tables", "fenced_code"],
        )
        self.update_panel(panel_id, document.id, content_html, verbose=verbose)

        return panel_id
