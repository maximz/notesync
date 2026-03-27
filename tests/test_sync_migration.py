"""
Migration round-trip tests for the sync database.

These tests verify that schema migrations work correctly by:
1. Creating a database with a prior schema version
2. Opening it via SyncDatabase (triggering migration)
3. Reading pre-existing rows (verifying defaults)
4. Writing new rows and reading them back (verifying field mapping)

IMPORTANT: When adding a new column to synced_documents, add a new test
class here that starts from the schema *before* your change and verifies
the full write-then-read round-trip after migration. See CLAUDE.md for
the full checklist.
"""

import sqlite3
from pathlib import Path

import pytest

from notesync.sync import SyncDatabase


def _create_v1_database(db_path: str) -> None:
    """Create a database with the original schema (no panel_count column)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE synced_documents (
            doc_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            file_path TEXT NOT NULL,
            synced_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_updated_at ON synced_documents(updated_at)
        """
    )
    conn.commit()
    conn.close()


def _insert_v1_row(db_path: str, doc_id: str = "old-doc-1") -> None:
    """Insert a row using the v1 schema (no panel_count)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO synced_documents (doc_id, title, created_at, updated_at, file_path, synced_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (doc_id, "Old Meeting", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z",
         "Uncategorized/old.md", "2026-01-03T00:00:00Z"),
    )
    conn.commit()
    conn.close()


class TestMigrationV1ToV2PanelCount:
    """Test migration from v1 (no panel_count) to v2 (with panel_count)."""

    def test_migration_adds_panel_count_column(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        _create_v1_database(db_path)

        # Opening SyncDatabase triggers migration
        SyncDatabase(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(synced_documents)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "panel_count" in columns

    def test_pre_existing_rows_get_default_panel_count(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        _create_v1_database(db_path)
        _insert_v1_row(db_path)

        db = SyncDatabase(db_path)
        state = db.get_sync_state("old-doc-1")

        assert state is not None
        assert state.panel_count == 0
        assert isinstance(state.panel_count, int)
        # Verify other fields survived migration
        assert state.title == "Old Meeting"
        assert state.synced_at == "2026-01-03T00:00:00Z"

    def test_write_then_read_round_trip_after_migration(self, tmp_path: Path) -> None:
        """The critical test: write a record with panel_count after migration,
        read it back, and verify every field has the correct type and value."""
        db_path = str(tmp_path / "test.db")
        _create_v1_database(db_path)

        db = SyncDatabase(db_path)
        db.mark_synced(
            doc_id="new-doc-1",
            title="New Meeting",
            created_at="2026-03-01T10:00:00Z",
            updated_at="2026-03-01T11:00:00Z",
            file_path="Uncategorized/new.md",
            panel_count=2,
        )

        state = db.get_sync_state("new-doc-1")
        assert state is not None
        assert state.doc_id == "new-doc-1"
        assert state.title == "New Meeting"
        assert state.created_at == "2026-03-01T10:00:00Z"
        assert state.updated_at == "2026-03-01T11:00:00Z"
        assert state.file_path == "Uncategorized/new.md"
        assert isinstance(state.panel_count, int)
        assert state.panel_count == 2
        assert isinstance(state.synced_at, str)
        assert "T" in state.synced_at  # ISO format, not an integer

    def test_mark_many_synced_round_trip_after_migration(self, tmp_path: Path) -> None:
        """Verify mark_many_synced (dict-based) also round-trips correctly."""
        db_path = str(tmp_path / "test.db")
        _create_v1_database(db_path)

        db = SyncDatabase(db_path)
        db.mark_many_synced([
            {
                "doc_id": "batch-1",
                "title": "Batch Meeting 1",
                "created_at": "2026-03-01T10:00:00Z",
                "updated_at": "2026-03-01T11:00:00Z",
                "file_path": "Uncategorized/batch1.md",
                "panel_count": 1,
            },
            {
                "doc_id": "batch-2",
                "title": "Batch Meeting 2",
                "created_at": "2026-03-02T10:00:00Z",
                "updated_at": "2026-03-02T11:00:00Z",
                "file_path": "Uncategorized/batch2.md",
                "panel_count": 0,
            },
        ])

        state1 = db.get_sync_state("batch-1")
        state2 = db.get_sync_state("batch-2")

        assert state1.panel_count == 1
        assert isinstance(state1.panel_count, int)
        assert isinstance(state1.synced_at, str)
        assert "T" in state1.synced_at

        assert state2.panel_count == 0
        assert isinstance(state2.panel_count, int)
        assert isinstance(state2.synced_at, str)
        assert "T" in state2.synced_at

    def test_get_docs_without_panels_after_migration(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        _create_v1_database(db_path)
        _insert_v1_row(db_path, doc_id="old-no-panels")

        db = SyncDatabase(db_path)
        db.mark_synced(
            doc_id="new-with-panels",
            title="Has Panels",
            created_at="2026-03-01T10:00:00Z",
            updated_at="2026-03-01T11:00:00Z",
            file_path="Uncategorized/panels.md",
            panel_count=1,
        )

        no_panels = db.get_docs_without_panels()
        ids = {s.doc_id for s in no_panels}
        assert "old-no-panels" in ids
        assert "new-with-panels" not in ids


class TestFreshDatabase:
    """Verify a fresh database (no migration needed) also works correctly."""

    def test_fresh_db_round_trip(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "fresh.db")
        db = SyncDatabase(db_path)

        db.mark_synced(
            doc_id="fresh-1",
            title="Fresh Meeting",
            created_at="2026-03-01T10:00:00Z",
            updated_at="2026-03-01T11:00:00Z",
            file_path="Uncategorized/fresh.md",
            panel_count=3,
        )

        state = db.get_sync_state("fresh-1")
        assert state.panel_count == 3
        assert isinstance(state.panel_count, int)
        assert isinstance(state.synced_at, str)
        assert "T" in state.synced_at
