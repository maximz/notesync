"""
Sync state management for NoteSync.
Tracks which documents have been synced to avoid re-exporting unchanged notes.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Document, SyncState

# Buffer time after meeting end to account for transcript processing
# Re-sync if meeting ended within this buffer after our last sync
TRANSCRIPT_BUFFER_MINUTES = 30

# Maximum assumed meeting duration for meetings without explicit end time
MAX_MEETING_HOURS = 5

SYNC_DB_FILENAME = ".notesync-sync.db"


class SyncDatabase:
    """
    SQLite database for tracking sync state.
    Stores information about when each document was last synced.
    """

    def __init__(self, db_path: str):
        """
        Initialize the sync database.

        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path
        self._init_database()

    def _init_database(self):
        """Create the database schema if it doesn't exist"""
        # Ensure parent directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            # Create synced_documents table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS synced_documents (
                    doc_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    synced_at TEXT NOT NULL,
                    panel_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )

            # Create index on updated_at for faster queries
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_updated_at
                ON synced_documents(updated_at)
                """
            )

            # Graceful migration: add panel_count column if it doesn't exist
            # (for databases created before this column was added)
            cursor.execute("PRAGMA table_info(synced_documents)")
            columns = {row[1] for row in cursor.fetchall()}
            if "panel_count" not in columns:
                cursor.execute(
                    "ALTER TABLE synced_documents ADD COLUMN panel_count INTEGER DEFAULT 0"
                )

            conn.commit()
        finally:
            conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        return conn

    def get_synced_documents(self) -> Dict[str, SyncState]:
        """
        Get all synced documents from the database.

        Returns:
            Dictionary mapping document ID to SyncState
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT doc_id, title, created_at, updated_at, file_path, synced_at, panel_count
                FROM synced_documents
                """
            )

            result = {}
            for row in cursor.fetchall():
                result[row["doc_id"]] = self._row_to_sync_state(row)

            return result
        finally:
            conn.close()

    def get_sync_state(self, doc_id: str) -> Optional[SyncState]:
        """
        Get sync state for a specific document.

        Args:
            doc_id: The document ID

        Returns:
            SyncState if document has been synced, None otherwise
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT doc_id, title, created_at, updated_at, file_path, synced_at, panel_count
                FROM synced_documents
                WHERE doc_id = ?
                """,
                (doc_id,),
            )

            row = cursor.fetchone()
            if row:
                return self._row_to_sync_state(row)
            return None
        finally:
            conn.close()

    def should_sync(
        self,
        document: Document,
        force: bool = False,
        current_panel_count: Optional[int] = None,
    ) -> bool:
        """
        Determine if a document should be synced.
        A document should be synced if:
        - force is True (force re-sync)
        - It's never been synced before
        - Its updated_at timestamp is newer than the last sync
        - The meeting ended after we last synced it (transcript may have been incomplete)
        - Panels appeared since last sync (was 0, now > 0)

        Args:
            document: The document to check
            force: If True, always return True
            current_panel_count: If provided, compare against stored panel_count

        Returns:
            True if document should be synced, False otherwise
        """
        if force:
            return True

        sync_state = self.get_sync_state(document.id)

        # Never synced before
        if not sync_state:
            return True

        # Check if document was updated after last sync
        if sync_state.is_outdated(document.updated_at):
            return True

        # Re-sync if meeting ended after (or shortly before) we last synced
        # This catches transcripts that were still processing when first synced
        if self._meeting_ended_after_sync(document, sync_state):
            return True

        # Re-sync if panels appeared since last export (was 0, now > 0)
        if (
            current_panel_count is not None
            and sync_state.panel_count == 0
            and current_panel_count > 0
        ):
            return True

        return False

    def _meeting_ended_after_sync(self, document: Document, sync_state: SyncState) -> bool:
        """
        Check if a meeting ended after we last synced it.
        Includes a buffer to account for transcript processing delay.

        Uses (in order of preference):
        1. google_calendar_event.end.dateTime (explicit end time)
        2. google_calendar_event.start.dateTime + MAX_MEETING_HOURS
        3. document.created_at + MAX_MEETING_HOURS

        Args:
            document: The document to check
            sync_state: The sync state for this document

        Returns:
            True if meeting ended after (synced_at - buffer), False otherwise
        """
        try:
            synced_at = datetime.fromisoformat(sync_state.synced_at.replace("Z", "+00:00"))
            cutoff = synced_at - timedelta(minutes=TRANSCRIPT_BUFFER_MINUTES)

            # Try to get end time from calendar event
            end_dt = None
            if document.google_calendar_event:
                end_info = document.google_calendar_event.get("end", {})
                end_dt_str = end_info.get("dateTime")

                if end_dt_str:
                    end_dt = datetime.fromisoformat(end_dt_str.replace("Z", "+00:00"))
                else:
                    # Fall back to start time + max duration
                    start_info = document.google_calendar_event.get("start", {})
                    start_dt_str = start_info.get("dateTime")
                    if start_dt_str:
                        start_dt = datetime.fromisoformat(start_dt_str.replace("Z", "+00:00"))
                        end_dt = start_dt + timedelta(hours=MAX_MEETING_HOURS)

            # Fall back to created_at + max duration
            if end_dt is None:
                created_at = datetime.fromisoformat(document.created_at.replace("Z", "+00:00"))
                end_dt = created_at + timedelta(hours=MAX_MEETING_HOURS)

            # Re-sync if meeting ended after (synced_at - buffer)
            return end_dt >= cutoff
        except (ValueError, TypeError):
            return False

    def mark_synced(
        self,
        doc_id: str,
        title: str,
        created_at: str,
        updated_at: str,
        file_path: str,
        panel_count: int = 0,
    ):
        """
        Mark a document as synced.
        Updates the database with the current sync timestamp.

        Args:
            doc_id: The document ID
            title: Document title
            created_at: Document creation timestamp
            updated_at: Document update timestamp
            file_path: Path where the file was saved
            panel_count: Number of panels at time of sync
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            synced_at = datetime.utcnow().isoformat() + "Z"

            cursor.execute(
                """
                INSERT OR REPLACE INTO synced_documents
                (doc_id, title, created_at, updated_at, file_path, synced_at, panel_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, title, created_at, updated_at, file_path, synced_at, panel_count),
            )

            conn.commit()
        finally:
            conn.close()

    def mark_many_synced(self, sync_records: List[tuple]):
        """
        Mark multiple documents as synced in a single transaction.
        More efficient than calling mark_synced() repeatedly.

        Args:
            sync_records: List of tuples (doc_id, title, created_at, updated_at, file_path, panel_count)
        """
        if not sync_records:
            return

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            synced_at = datetime.utcnow().isoformat() + "Z"

            # Prepare records with synced_at
            records_with_sync_time = [
                (*record, synced_at) for record in sync_records
            ]

            cursor.executemany(
                """
                INSERT OR REPLACE INTO synced_documents
                (doc_id, title, created_at, updated_at, file_path, panel_count, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                records_with_sync_time,
            )

            conn.commit()
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about synced documents.

        Returns:
            Dictionary with stats:
            - total_documents: Total number of synced documents
            - last_sync_time: Most recent sync timestamp
            - oldest_sync_time: Oldest sync timestamp
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Count total documents
            cursor.execute("SELECT COUNT(*) as count FROM synced_documents")
            total = cursor.fetchone()["count"]

            # Get last sync time
            cursor.execute(
                """
                SELECT MAX(synced_at) as last_sync
                FROM synced_documents
                """
            )
            row = cursor.fetchone()
            last_sync = row["last_sync"] if row else None

            # Get oldest sync time
            cursor.execute(
                """
                SELECT MIN(synced_at) as oldest_sync
                FROM synced_documents
                """
            )
            row = cursor.fetchone()
            oldest_sync = row["oldest_sync"] if row else None

            return {
                "total_documents": total,
                "last_sync_time": last_sync,
                "oldest_sync_time": oldest_sync,
            }
        finally:
            conn.close()

    def get_sync_state_by_path(self, file_path: str) -> Optional[SyncState]:
        """
        Get sync state for a document by its file path.

        Args:
            file_path: The file path (can be relative or absolute)

        Returns:
            SyncState if found, None otherwise
        """
        normalized_query = self._normalize_path(file_path)
        if not normalized_query:
            return None

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Try exact match first
            cursor.execute(
                """
                SELECT doc_id, title, created_at, updated_at, file_path, synced_at, panel_count
                FROM synced_documents
                WHERE file_path = ?
                """,
                (file_path,),
            )

            row = cursor.fetchone()
            if row:
                return self._row_to_sync_state(row)

            # Fallback: deterministic suffix match for relative paths without SQL wildcards.
            cursor.execute(
                """
                SELECT doc_id, title, created_at, updated_at, file_path, synced_at, panel_count
                FROM synced_documents
                """
            )

            matches = []
            for candidate in cursor.fetchall():
                candidate_path = self._normalize_path(candidate["file_path"])
                if self._path_suffix_matches(candidate_path, normalized_query):
                    matches.append(candidate)

            if len(matches) == 1:
                return self._row_to_sync_state(matches[0])

            if len(matches) > 1:
                raise ValueError(
                    f"Multiple synced notes match path '{file_path}'. "
                    "Please provide a more specific path."
                )

            return None
        finally:
            conn.close()

    @staticmethod
    def _row_to_sync_state(row: sqlite3.Row) -> SyncState:
        return SyncState(
            doc_id=row["doc_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            file_path=row["file_path"],
            synced_at=row["synced_at"],
            panel_count=row["panel_count"] or 0,
        )

    def get_docs_without_panels(self) -> List[SyncState]:
        """
        Get all synced documents that had no panels at last sync.
        Used to identify docs that may need re-export if panels have since appeared.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT doc_id, title, created_at, updated_at, file_path, synced_at, panel_count
                FROM synced_documents
                WHERE panel_count = 0 OR panel_count IS NULL
                """
            )
            return [self._row_to_sync_state(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = path.strip().replace("\\", "/")
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized.rstrip("/")

    @staticmethod
    def _path_suffix_matches(candidate_path: str, query_path: str) -> bool:
        candidate_parts = [part for part in candidate_path.split("/") if part]
        query_parts = [part for part in query_path.split("/") if part]

        if not candidate_parts or not query_parts:
            return False
        if len(query_parts) > len(candidate_parts):
            return False

        return candidate_parts[-len(query_parts):] == query_parts

    def remove_synced_document(self, doc_id: str):
        """
        Remove a document from the sync state.
        Use this if a document was deleted or should be re-synced from scratch.

        Args:
            doc_id: The document ID to remove
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM synced_documents
                WHERE doc_id = ?
                """,
                (doc_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def clear_all(self):
        """
        Clear all sync state.
        USE WITH CAUTION: This will cause all documents to be re-synced on next run.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM synced_documents")
            conn.commit()
        finally:
            conn.close()
