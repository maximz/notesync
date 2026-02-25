"""
Data models for NoteSync.
Pydantic models for Granola API and local cache payloads.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Notes Structure (ProseMirror-based)
# ============================================================================


class NodeAttrs(BaseModel):
    """Attributes for a content node"""

    # All fields are optional since different node types have different attributes
    id: Optional[str] = None
    isSelected: Optional[bool] = None
    level: Optional[int] = None
    href: Optional[str] = None
    timestamp: Optional[str] = None
    timestamp_to: Optional[str] = Field(None, alias="timestamp-to")
    tight: Optional[bool] = None  # For list nodes

    model_config = ConfigDict(extra="allow")  # Allow additional attributes we don't know about


class ContentNode(BaseModel):
    """
    Recursive content node structure for ProseMirror documents.
    Can represent paragraphs, headings, lists, text, etc.
    """

    type: str
    attrs: Optional[NodeAttrs] = None
    content: Optional[List["ContentNode"]] = None
    text: Optional[str] = None
    marks: Optional[List[Dict[str, Any]]] = None  # Text formatting marks

    model_config = ConfigDict(extra="allow")  # Allow additional fields we don't know about


class Attachment(BaseModel):
    """Document attachment"""

    content: str
    kind: str
    name: str


class DocumentStructure(BaseModel):
    """Structured document representation for panels"""

    attachments: List[Attachment] = Field(default_factory=list)
    type: Optional[str] = None
    content: Optional[List[ContentNode]] = None


class Notes(BaseModel):
    """
    Root notes structure (ProseMirror document).
    Contains an array of content nodes.
    """

    type: str = "doc"
    content: List[ContentNode] = Field(default_factory=list)


# ============================================================================
# Panel Structure (AI-Generated Content)
# ============================================================================


class PanelContent(BaseModel):
    """
    Content for a panel in Granola.
    Panels contain AI-generated content like summaries, action items, etc.
    """

    original_content: Optional[str] = ""
    content: Optional[Union[str, DocumentStructure]] = None  # Can be HTML string or structured content

    model_config = ConfigDict(extra="allow")  # Allow additional fields like document_id, title, etc.


# Panel ID and Document ID are just strings
PanelId = str
DocId = str


# ============================================================================
# People & Attendees
# ============================================================================


class PersonDetails(BaseModel):
    """Details about a person"""

    fullName: Optional[str] = None
    avatar: Optional[str] = None
    linkedin_handle: Optional[str] = None
    company_name: Optional[str] = None
    title: Optional[str] = None


class Attendee(BaseModel):
    """Meeting attendee information"""

    name: Optional[str] = None
    email: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class Creator(BaseModel):
    """Meeting creator information"""

    name: str
    email: str
    details: Optional[Dict[str, Any]] = None


class People(BaseModel):
    """People involved in a meeting"""

    creator: Optional[Creator] = None
    attendees: List[Attendee] = Field(default_factory=list)


# ============================================================================
# Main Document Model
# ============================================================================


class Document(BaseModel):
    """
    Main document (note) model matching the Granola API response.
    Represents a single meeting note/transcript.
    """

    id: str
    title: Optional[str] = "Untitled"
    created_at: str
    updated_at: str
    user_id: str

    # Note content in various formats
    notes: Optional[Notes] = None
    notes_markdown: Optional[str] = None
    notes_plain: Optional[str] = None

    # Meeting metadata
    transcribe: Optional[bool] = False
    google_calendar_event: Optional[Dict[str, Any]] = None
    people: Optional[People] = None
    creation_source: Optional[str] = None

    # Sharing and visibility
    public: Optional[bool] = False
    has_shareable_link: Optional[bool] = False
    sharing_link_visibility: Optional[str] = "private"
    show_private_notes: Optional[bool] = None
    privacy_mode_enabled: Optional[bool] = None

    # Workspace and organization
    workspace_id: Optional[str] = None
    visibility: Optional[str] = None

    # Other optional fields
    cloned_from: Optional[str] = None
    deleted_at: Optional[str] = None
    type: Optional[str] = None
    overview: Optional[str] = None
    chapters: Optional[Any] = None
    meeting_end_count: Optional[int] = 0
    selected_template: Optional[Any] = None
    valid_meeting: Optional[bool] = None
    summary: Optional[str] = None
    affinity_note_id: Optional[str] = None
    hubspot_note_url: Optional[str] = None
    subscription_plan_id: Optional[str] = None
    status: Optional[str] = None
    external_transcription_id: Optional[str] = None
    audio_file_handle: Optional[str] = None
    notification_config: Optional[Any] = None

    def get_created_datetime(self) -> datetime:
        """Parse created_at as a datetime object"""
        # Handle both formats: "2025-10-25T14:30:45.123Z" and "2025-10-25T14:30:45+00:00"
        timestamp = self.created_at.replace("Z", "+00:00")
        return datetime.fromisoformat(timestamp)

    def get_updated_datetime(self) -> datetime:
        """Parse updated_at as a datetime object"""
        timestamp = self.updated_at.replace("Z", "+00:00")
        return datetime.fromisoformat(timestamp)


class GetDocumentsResponse(BaseModel):
    """Response from the /v2/get-documents API endpoint"""

    docs: List[Document] = Field(default_factory=list)
    deleted: List[str] = Field(default_factory=list)


# ============================================================================
# Transcript Models
# ============================================================================


class TranscriptSegment(BaseModel):
    """
    A single segment of transcript.
    Represents a continuous chunk of speech from one source.
    """

    id: str
    document_id: str
    start_timestamp: str
    end_timestamp: str
    text: str
    source: str  # "microphone" or "system"
    is_final: bool = True

    def get_start_time(self) -> float:
        """Parse start_timestamp as float (seconds)"""
        try:
            return float(self.start_timestamp)
        except (ValueError, TypeError):
            return 0.0

    def get_end_time(self) -> float:
        """Parse end_timestamp as float (seconds)"""
        try:
            return float(self.end_timestamp)
        except (ValueError, TypeError):
            return 0.0

    def is_user_speech(self) -> bool:
        """Check if this segment is from the user's microphone"""
        return self.source == "microphone"

    def is_system_audio(self) -> bool:
        """Check if this segment is from system audio"""
        return self.source == "system"


# ============================================================================
# Folder Models
# ============================================================================


class FolderIcon(BaseModel):
    """Icon metadata for a folder"""

    type: str
    color: str
    value: str


class FolderMember(BaseModel):
    """Member of a shared folder"""

    user_id: str
    name: str
    email: str
    avatar: str
    role: str
    created_at: str


class Folder(BaseModel):
    """
    Folder (document list) containing multiple notes.
    Folders help organize notes by project, team, etc.
    """

    id: str
    title: str
    description: Optional[str] = None
    icon: Optional[FolderIcon] = None
    visibility: str = "private"
    created_at: str
    updated_at: str
    deleted_at: Optional[str] = None
    workspace_id: Optional[str] = None
    preset: Optional[str] = None
    is_favourited: bool = False
    user_role: str = "owner"
    sharing_link_visibility: str = "private"
    members: List[FolderMember] = Field(default_factory=list)
    invites: List[Any] = Field(default_factory=list)
    slack_channel: Optional[str] = None
    is_shared: bool = False
    document_ids: List[str] = Field(default_factory=list)


class FoldersResponse(BaseModel):
    """Response from the /v1/get-document-lists-metadata API endpoint"""

    lists: Dict[str, Folder] = Field(default_factory=dict)


# ============================================================================
# Sync State Model (for local database)
# ============================================================================


class SyncState(BaseModel):
    """
    Local sync state for a document.
    Tracks when a document was last synced to disk.
    """

    doc_id: str
    title: str
    created_at: str
    updated_at: str
    file_path: str
    synced_at: str  # ISO timestamp of when we synced it

    def is_outdated(self, api_updated_at: str) -> bool:
        """
        Check if the local file is outdated compared to the API version.
        Returns True if the API version is newer.
        """
        # Parse timestamps
        local_time = datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
        api_time = datetime.fromisoformat(api_updated_at.replace("Z", "+00:00"))
        return api_time > local_time


# ============================================================================
# Cache Models (from cache-v3.json)
# ============================================================================


class CacheState(BaseModel):
    """
    Represents the application state from Granola's cache-v3.json.
    The cache contains AI-generated panel content and other local data.
    """

    documentPanels: Dict[str, Dict[str, Any]] = Field(default_factory=dict)  # doc_id -> panel_id -> panel_data


class GranolaCache(BaseModel):
    """Root cache structure from cache-v3.json"""

    state: Optional[CacheState] = None
    # There are many other fields in the cache, but we only need documentPanels


# Update forward references for recursive models
ContentNode.model_rebuild()
