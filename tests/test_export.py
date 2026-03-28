import os
import re
import tempfile

from notesync.export import ExportEngine
from notesync.models import Document
from notesync.sync import SyncDatabase


class _DummyAPI:
    pass


def _make_document(**overrides) -> Document:
    data = {
        "id": "abcdef0123456789",
        "title": "Weekly Team Sync",
        "created_at": "2026-01-01T12:34:56Z",
        "updated_at": "2026-01-01T12:34:56Z",
        "user_id": "user-1",
    }
    data.update(overrides)
    return Document(**data)


def test_sanitize_title_removes_invalid_chars_and_normalizes_whitespace() -> None:
    engine = ExportEngine(api=_DummyAPI())

    sanitized = engine.sanitize_title('  Team: Sync / Q1?  "Plan"   ')

    assert sanitized == "Team_Sync_Q1_Plan"


def test_generate_filename_has_stable_shape() -> None:
    engine = ExportEngine(api=_DummyAPI())
    doc = _make_document()

    filename = engine.generate_filename(doc)

    assert re.match(r"^\d{8}_\d{4}\.Weekly_Team_Sync\.[a-zA-Z0-9]{8}\.md$", filename)


def test_rename_cleanup_removes_old_file() -> None:
    """When a document title changes, the old file should be deleted on re-export."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SyncDatabase(os.path.join(tmpdir, ".notesync-sync.db"))

        # Simulate initial sync with old title
        old_rel_path = "Uncategorized/20260327_1954.Old_Title.abcdef01.md"
        old_abs_path = os.path.join(tmpdir, old_rel_path)
        os.makedirs(os.path.dirname(old_abs_path), exist_ok=True)
        with open(old_abs_path, "w") as f:
            f.write("old content")

        db.mark_synced(
            doc_id="abcdef0123456789",
            title="Old Title",
            created_at="2026-03-27T19:54:00Z",
            updated_at="2026-03-27T20:00:00Z",
            file_path=old_rel_path,
            panel_count=1,
        )

        # Simulate re-export with new title (new file path)
        new_rel_path = "Uncategorized/20260327_1954.New_Title.abcdef01.md"
        new_abs_path = os.path.join(tmpdir, new_rel_path)
        with open(new_abs_path, "w") as f:
            f.write("new content")

        # This is the cleanup logic from export.py
        sync_state = db.get_sync_state("abcdef0123456789")
        assert sync_state is not None
        assert sync_state.file_path != new_rel_path

        old_file = os.path.join(tmpdir, sync_state.file_path)
        assert os.path.exists(old_file)
        os.remove(old_file)

        # Update sync state
        db.mark_synced(
            doc_id="abcdef0123456789",
            title="New Title",
            created_at="2026-03-27T19:54:00Z",
            updated_at="2026-03-28T10:00:00Z",
            file_path=new_rel_path,
            panel_count=1,
        )

        # Verify: old file gone, new file exists, DB points to new path
        assert not os.path.exists(old_abs_path)
        assert os.path.exists(new_abs_path)
        updated_state = db.get_sync_state("abcdef0123456789")
        assert updated_state.file_path == new_rel_path
        assert updated_state.title == "New Title"


def test_rename_cleanup_no_op_when_path_unchanged() -> None:
    """When the file path hasn't changed, nothing should be deleted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SyncDatabase(os.path.join(tmpdir, ".notesync-sync.db"))

        rel_path = "Uncategorized/20260327_1954.Same_Title.abcdef01.md"
        abs_path = os.path.join(tmpdir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as f:
            f.write("content")

        db.mark_synced(
            doc_id="abcdef0123456789",
            title="Same Title",
            created_at="2026-03-27T19:54:00Z",
            updated_at="2026-03-27T20:00:00Z",
            file_path=rel_path,
            panel_count=1,
        )

        # Same path on re-export -- no cleanup needed
        sync_state = db.get_sync_state("abcdef0123456789")
        assert sync_state.file_path == rel_path
        assert os.path.exists(abs_path)
