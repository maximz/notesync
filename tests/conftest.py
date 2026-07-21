from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _isolate_token_dir(tmp_path, monkeypatch):
    """
    Redirect the notesync token store into a per-test temp dir so no test can
    write to the real ~/.cache/notesync/tokens. Any test needing the path can
    still set NOTESYNC_TOKEN_DIR itself; that override runs after this and wins.
    """
    monkeypatch.setenv("NOTESYNC_TOKEN_DIR", str(tmp_path / "notesync-token-store"))
