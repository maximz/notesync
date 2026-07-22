"""Tests for notesync.api client-version detection."""
import platform
import plistlib
from pathlib import Path

from notesync import api


def _write_plist(path: Path, version) -> None:
    data = {}
    if version is not None:
        data["CFBundleShortVersionString"] = version
    with open(path, "wb") as f:
        plistlib.dump(data, f)


def test_detect_client_version_reads_installed_plist(tmp_path, monkeypatch):
    """On macOS with a readable Info.plist, use the real installed version."""
    plist = tmp_path / "Info.plist"
    _write_plist(plist, "9.9.9")
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(api, "_GRANOLA_INFO_PLIST", plist)
    assert api._detect_client_version() == "9.9.9"


def test_detect_client_version_falls_back_off_macos(tmp_path, monkeypatch):
    plist = tmp_path / "Info.plist"
    _write_plist(plist, "9.9.9")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(api, "_GRANOLA_INFO_PLIST", plist)
    assert api._detect_client_version() == api._FALLBACK_CLIENT_VERSION


def test_detect_client_version_falls_back_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(api, "_GRANOLA_INFO_PLIST", tmp_path / "nope.plist")
    assert api._detect_client_version() == api._FALLBACK_CLIENT_VERSION


def test_detect_client_version_falls_back_when_key_absent(tmp_path, monkeypatch):
    """A plist without CFBundleShortVersionString falls back cleanly."""
    plist = tmp_path / "Info.plist"
    _write_plist(plist, None)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(api, "_GRANOLA_INFO_PLIST", plist)
    assert api._detect_client_version() == api._FALLBACK_CLIENT_VERSION


def test_get_user_agent_includes_detected_client_version():
    ua = api.get_user_agent()
    assert f"Granola/{api.API_CONFIG['CLIENT_VERSION']}" in ua
