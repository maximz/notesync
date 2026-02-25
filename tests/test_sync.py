from pathlib import Path

import pytest

from notesync.sync import SYNC_DB_FILENAME, SyncDatabase


def _create_db(tmp_path: Path) -> SyncDatabase:
    db_path = tmp_path / SYNC_DB_FILENAME
    return SyncDatabase(str(db_path))


def test_get_sync_state_by_path_exact_match(tmp_path: Path) -> None:
    db = _create_db(tmp_path)
    db.mark_synced(
        doc_id="doc-1",
        title="Title 1",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        file_path="Team/20260101_1200.Note.doc-1.md",
    )

    state = db.get_sync_state_by_path("Team/20260101_1200.Note.doc-1.md")
    assert state is not None
    assert state.doc_id == "doc-1"


def test_get_sync_state_by_path_suffix_match_with_windows_style_input(tmp_path: Path) -> None:
    db = _create_db(tmp_path)
    db.mark_synced(
        doc_id="doc-2",
        title="Title 2",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-02T00:00:00Z",
        file_path="Engineering/Subfolder/20260102_1200.Note.doc-2.md",
    )

    state = db.get_sync_state_by_path(r"Subfolder\20260102_1200.Note.doc-2.md")
    assert state is not None
    assert state.doc_id == "doc-2"


def test_get_sync_state_by_path_ambiguous_suffix_raises(tmp_path: Path) -> None:
    db = _create_db(tmp_path)
    common_name = "20260103_1200.Note.doc.md"

    db.mark_synced(
        doc_id="doc-a",
        title="Title A",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-03T00:00:00Z",
        file_path=f"FolderA/{common_name}",
    )
    db.mark_synced(
        doc_id="doc-b",
        title="Title B",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-03T00:00:00Z",
        file_path=f"FolderB/{common_name}",
    )

    with pytest.raises(ValueError, match="Multiple synced notes match path"):
        db.get_sync_state_by_path(common_name)
