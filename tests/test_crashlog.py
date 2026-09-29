"""Crash-log handling: unhandled exceptions must land on disk, not vanish."""

import sys

import pytest

from media_organizer.crashlog import (MAX_LOG_BYTES, crash_log_path,
                                      install_crash_handling)


@pytest.fixture
def restore_excepthook():
    old = sys.excepthook
    yield
    sys.excepthook = old


def test_crash_log_path_under_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = crash_log_path()
    assert path == tmp_path / "MediaOrganizer" / "crash.log"
    assert path.parent.is_dir()


def test_excepthook_writes_traceback(tmp_path, restore_excepthook):
    log = tmp_path / "crash.log"
    install_crash_handling(log, show_dialog=False)
    try:
        raise ValueError("boom-crash-test")
    except ValueError:
        exc_type, exc_value, exc_tb = sys.exc_info()
    sys.excepthook(exc_type, exc_value, exc_tb)
    text = log.read_text(encoding="utf-8")
    assert "unhandled exception" in text
    assert "ValueError: boom-crash-test" in text


def test_oversized_log_is_truncated(tmp_path, restore_excepthook):
    log = tmp_path / "crash.log"
    log.write_bytes(b"x" * (MAX_LOG_BYTES + 1))
    install_crash_handling(log, show_dialog=False)
    try:
        raise RuntimeError("fresh")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    text = log.read_text(encoding="utf-8")
    assert len(text) < MAX_LOG_BYTES
    assert "RuntimeError: fresh" in text


def test_install_survives_unwritable_location(tmp_path, monkeypatch,
                                              restore_excepthook):
    # crash handling must never break startup, even if the log can't open:
    # here a *file* sits where the MediaOrganizer folder would be created
    blocker = tmp_path / "MediaOrganizer"
    blocker.write_text("not a directory")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    install_crash_handling(show_dialog=False)  # must not raise
