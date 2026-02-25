import re

from notesync.export import ExportEngine
from notesync.models import Document


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
