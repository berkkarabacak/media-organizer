"""Last-resort crash handling: unhandled exceptions go to an on-disk log.

The frozen build is windowed (no console), so without this an unexpected
error would kill the app silently and leave nothing behind for support to
diagnose. Qt-free on import; PySide6 is only touched lazily inside the hook,
after an error has already occurred.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO

#: crash log is truncated when it grows past this size
MAX_LOG_BYTES = 1_000_000


def crash_log_path() -> Path:
    """Per-user log location: %LOCALAPPDATA%/MediaOrganizer/crash.log."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    folder = root / "MediaOrganizer"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "crash.log"


def _open_log(path: Path) -> Optional[TextIO]:
    try:
        mode = "a"
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            mode = "w"  # keep the log bounded: drop ancient history
        return open(path, mode, encoding="utf-8", buffering=1)
    except OSError:
        return None


def install_crash_handling(log_path: Optional[Path] = None,
                           show_dialog: bool = True) -> None:
    """Install sys.excepthook + faulthandler writing to the crash log.

    show_dialog=False skips the user-facing message box (used by tests,
    where a modal dialog could block an offscreen run).
    """
    try:
        path = log_path or crash_log_path()
        log_file = _open_log(path)
    except Exception:
        log_file, path = None, None  # crash handling must never break startup

    if log_file is not None:
        try:
            faulthandler.enable(file=log_file)  # hard crashes (segfaults)
        except Exception:
            pass

    def excepthook(exc_type, exc_value, exc_tb):
        if log_file is not None and path is not None:
            try:
                log_file.write(
                    f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} "
                    f"unhandled exception ===\n"
                )
                traceback.print_exception(exc_type, exc_value, exc_tb,
                                          file=log_file)
            except Exception:
                pass
        # best effort: tell the user instead of vanishing silently
        if show_dialog:
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox
                if QApplication.instance() is not None:
                    QMessageBox.critical(
                        None, "Media Organizer",
                        "Media Organizer hit an unexpected error.\n"
                        "The app will try to keep running; if it misbehaves, "
                        "please restart it.\n\n"
                        f"Details were saved to:\n{path}\n\n{exc_value}")
            except Exception:
                pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook
