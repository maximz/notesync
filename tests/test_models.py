"""
Regression tests for Pydantic models against real-world Granola API payloads.
"""

from notesync.models import Folder, FolderMember, FoldersResponse


def test_folder_member_accepts_missing_avatar() -> None:
    # Granola's API omits `avatar` for members without a profile picture
    # (pending invites, accounts that never set one). Used to raise
    # ValidationError; folder-member fields beyond user_id are now optional.
    member = FolderMember(
        user_id="u-1",
        name="Alice",
        email="alice@example.com",
        role="member",
        created_at="2026-01-01T00:00:00Z",
    )
    assert member.avatar is None


def test_folders_response_with_sparse_members_parses() -> None:
    payload = {
        "lists": {
            "f-1": {
                "id": "f-1",
                "title": "Project X",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "members": [
                    {
                        "user_id": "u-1",
                        "name": "Alice",
                        "email": "alice@example.com",
                        "role": "owner",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    {"user_id": "u-2"},
                ],
            }
        }
    }
    parsed = FoldersResponse(**payload)
    folder = parsed.lists["f-1"]
    assert isinstance(folder, Folder)
    assert len(folder.members) == 2
    assert folder.members[0].avatar is None
    assert folder.members[1].name is None
    assert folder.members[1].email is None
