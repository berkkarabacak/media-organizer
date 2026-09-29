"""Operation log + undo support.

Every executed run writes a JSON log of the file operations performed, so a
run can be undone later (files moved back to their original locations).
"""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIRNAME = ".media_organizer"
LOG_FILENAME = "operation_log.json"

#: Shown before a run and again when the user asks to undo. There is one log
#: per destination, so a later organize replaces it.
UNDO_LIMITATION = (
    "Only the most recent run in this folder can be undone. "
    "Organizing again replaces the undo log, and the earlier run "
    "cannot be undone. Undo will not delete the only remaining copy of a file."
)


@dataclass
class Operation:
    action: str  # "copy" | "move"
    source: str
    destination: str
    status: str = "pending"  # pending | done | skipped | error
    error: str = ""
    size: int = -1          # bytes written; -1 on logs from older versions
    sha256: str = ""        # of the bytes written; empty on older logs


@dataclass
class RunLog:
    started_at: str
    dest_dir: str
    operations: list[Operation] = field(default_factory=list)
    undone: bool = False

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "dest_dir": self.dest_dir,
            "undone": self.undone,
            "operations": [vars(op) for op in self.operations],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunLog":
        return cls(
            started_at=data["started_at"],
            dest_dir=data["dest_dir"],
            operations=[Operation(**op) for op in data.get("operations", [])],
            undone=data.get("undone", False),
        )


def log_path_for(dest_dir: Path | str) -> Path:
    return Path(dest_dir) / LOG_DIRNAME / LOG_FILENAME


def new_log(dest_dir: Path | str) -> RunLog:
    return RunLog(started_at=datetime.now().isoformat(timespec="seconds"),
                  dest_dir=str(dest_dir))


def save_log(log: RunLog, dest_dir: Path | str) -> Path:
    path = log_path_for(dest_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log.to_dict(), indent=2), encoding="utf-8")
    return path


def load_log(dest_dir: Path | str) -> Optional[RunLog]:
    path = log_path_for(dest_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunLog.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _move_back(src: Path, dest: Path) -> None:
    """Move `dest` back to `src`, never overwriting anything at `src`."""
    target = src
    if target.exists():
        stem, suffix = src.stem, src.suffix
        n = 0
        while target.exists():
            n += 1
            target = src.with_name(f"{stem}_restored_{n}{suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dest), str(target))


def _send_to_recycle_bin(path: Path) -> bool:
    """Move `path` to the Windows Recycle Bin.

    Returns False when that is not available (any other OS, or the shell
    call failed). The caller then decides whether a hard delete is safe.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p),
            ]

        fo_delete = 3
        fof_silent = 0x0004
        fof_noconfirmation = 0x0010
        fof_allowundo = 0x0040
        fof_noerrorui = 0x0400

        try:
            target = str(path.resolve())
        except OSError:
            target = str(path)
        # SHFileOperation wants a double-null-terminated list of paths.
        buf = ctypes.create_unicode_buffer(target + "\0")
        op = SHFILEOPSTRUCTW()
        op.wFunc = fo_delete
        op.pFrom = ctypes.cast(buf, ctypes.c_wchar_p)
        op.fFlags = fof_allowundo | fof_noconfirmation | fof_silent | fof_noerrorui
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return result == 0 and not op.fAnyOperationsAborted
    except Exception:
        return False


def _same_path(src: Path, dest: Path) -> bool:
    try:
        return src.resolve() == dest.resolve()
    except OSError:
        return os.path.normcase(str(src)) == os.path.normcase(str(dest))


def _identical(src: Path, dest: Path) -> bool:
    """True when `dest` is still a byte-for-byte copy of `src`."""
    try:
        if not src.is_file() or not dest.is_file():
            return False
        if src.stat().st_size != dest.stat().st_size:
            return False
        return filecmp.cmp(src, dest, shallow=False)
    except OSError:
        return False


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _still_the_moved_file(dest: Path, op: Operation) -> bool:
    """False when `dest` is no longer the file this run moved."""
    try:
        if not dest.is_file():
            return False
        if op.size >= 0 and dest.stat().st_size != op.size:
            return False
        if op.sha256 and _sha256(dest) != op.sha256:
            return False
    except OSError:
        return False
    return True


def _discard_copied_file(dest: Path) -> bool:
    """Remove a copy whose identical original is still in place.

    On Windows the file goes to the Recycle Bin. Elsewhere it is deleted,
    which only happens after the caller has checked the original still matches.
    """
    if _send_to_recycle_bin(dest):
        return True
    try:
        dest.unlink()
        return True
    except OSError:
        return False


def undo_result_message(result: dict) -> str:
    """Plain-language result for the undo dialog."""
    msg = (
        f"Undo complete: {result.get('undone', 0)} files restored, "
        f"{result.get('skipped', 0)} skipped, {result.get('failed', 0)} failed."
    )
    kept = result.get("kept", 0)
    if kept:
        msg += (
            f"\n\n{kept} left in place so a file would not be lost. "
            "A copy is removed only when an identical original is still "
            "there. If this file is the only copy left, or it no longer "
            "matches what the run wrote, it stays."
        )
    return msg


def undo_log(log: RunLog, dest_dir: Path | str) -> dict:
    """Undo every successful operation in the log. Returns a summary dict.

    A copied file is removed only when the original is still there and the
    two files still match, so undo cannot destroy the only remaining copy
    or a file the user has since replaced. On Windows that removal goes to
    the Recycle Bin. A moved file is moved back only when it is still the
    file this run wrote.

    ``kept`` counts files left in place on purpose. The log stays available
    for another attempt when anything was kept or failed.
    """
    undone = skipped = failed = kept = 0
    # Reverse order so suffix chains unwind cleanly
    for op in reversed(log.operations):
        if op.status != "done":
            skipped += 1
            continue
        src, dest = Path(op.source), Path(op.destination)
        try:
            if op.action == "copy":
                if not dest.exists():
                    skipped += 1
                    continue
                # Source and destination are the same file. Removing it would
                # delete the only copy.
                if _same_path(src, dest):
                    kept += 1
                    continue
                if not _identical(src, dest):
                    # Original is gone, or one of the two files has changed.
                    kept += 1
                    continue
                if _discard_copied_file(dest):
                    undone += 1
                else:
                    failed += 1
            elif op.action == "move":
                if not dest.exists():
                    skipped += 1
                    continue
                if not _still_the_moved_file(dest, op):
                    kept += 1
                    continue
                _move_back(src, dest)
                undone += 1
            else:
                skipped += 1
        except OSError:
            failed += 1
    log.undone = kept == 0 and failed == 0
    save_log(log, dest_dir)
    return {"undone": undone, "skipped": skipped, "failed": failed, "kept": kept}
