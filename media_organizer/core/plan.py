"""Operation log + undo support.

Every executed run writes a JSON log of the file operations performed, so a
run can be undone later (files moved back to their original locations).

One destination keeps a stack of recent runs. Organizing again archives the
previous log instead of replacing it. The newest ``UNDO_STACK_LIMIT`` runs
can be undone, newest first. Anything older is moved aside under
``history/retired`` and is not offered for undo; those files are not deleted.
"""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIRNAME = ".media_organizer"
LOG_FILENAME = "operation_log.json"
HISTORY_DIRNAME = "history"
RETIRED_DIRNAME = "retired"

#: How many organize runs in one destination folder can be undone, newest first.
UNDO_STACK_LIMIT = 8

#: Shown on step 3 and again when the user asks to undo.
UNDO_LIMITATION = (
    f"The last {UNDO_STACK_LIMIT} organize runs in this folder can be undone, "
    "newest first. Organizing again keeps those undo logs. "
    "A run older than that stays on disk but cannot be undone from here. "
    "Undo will not delete the only remaining copy of a file."
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
    run_id: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
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
            run_id=data.get("run_id", "") or "",
        )


def log_path_for(dest_dir: Path | str) -> Path:
    return Path(dest_dir) / LOG_DIRNAME / LOG_FILENAME


def history_dir_for(dest_dir: Path | str) -> Path:
    return Path(dest_dir) / LOG_DIRNAME / HISTORY_DIRNAME


def new_log(dest_dir: Path | str) -> RunLog:
    return RunLog(started_at=datetime.now().isoformat(timespec="seconds"),
                  dest_dir=str(dest_dir),
                  run_id=uuid.uuid4().hex)


def _read_log(path: Path) -> Optional[RunLog]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunLog.from_dict(data)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _write_log(log: RunLog, path: Path) -> None:
    """Write `log` by replacing `path`. A failed write leaves the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(log.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _stable_key(log: RunLog) -> tuple:
    """Identity that does not change when the log is marked undone."""
    if log.run_id:
        return ("id", log.run_id)
    ops = tuple((op.action, op.source, op.destination, op.status)
                for op in log.operations)
    return ("legacy", log.started_at, ops)


def _numbered_history(history: Path) -> list[Path]:
    """Archived logs, oldest first. ``retired/`` is not included."""
    found: list[tuple[int, Path]] = []
    if not history.is_dir():
        return []
    for path in history.glob("*.json"):
        stem = path.stem.split("_", 1)[0]
        if stem.isdigit():
            found.append((int(stem), path))
    found.sort()
    return [path for _, path in found]


def _log_files(dest_dir: Path, *, include_retired: bool) -> list[Path]:
    """Newest first. The current log, then archived history."""
    files: list[Path] = []
    current = log_path_for(dest_dir)
    if current.is_file():
        files.append(current)
    files.extend(reversed(_numbered_history(history_dir_for(dest_dir))))
    if include_retired:
        retired = history_dir_for(dest_dir) / RETIRED_DIRNAME
        if retired.is_dir():
            files.extend(sorted(p for p in retired.glob("*.json") if p.is_file()))
    return files


def list_run_logs(dest_dir: Path | str) -> list[RunLog]:
    """Saved runs in this folder, newest first.

    Includes runs already marked undone. Retired logs (older than
    ``UNDO_STACK_LIMIT``) are not included.
    """
    logs = []
    for path in _log_files(Path(dest_dir), include_retired=False):
        log = _read_log(path)
        if log is not None:
            logs.append(log)
    return logs


def load_log(dest_dir: Path | str) -> Optional[RunLog]:
    """The newest run's log, including one that has already been undone."""
    logs = list_run_logs(dest_dir)
    return logs[0] if logs else None


def load_undoable_log(dest_dir: Path | str) -> Optional[RunLog]:
    """The newest run that has not been fully undone."""
    for log in list_run_logs(dest_dir):
        if not log.undone:
            return log
    return None


def _next_history_path(history: Path) -> Path:
    nums = []
    for path in _numbered_history(history):
        nums.append(int(path.stem.split("_", 1)[0]))
    n = max(nums, default=0) + 1
    target = history / f"{n:04d}.json"
    while target.exists():
        n += 1
        target = history / f"{n:04d}.json"
    return target


def _retire_file(history: Path, path: Path) -> None:
    """Move a log out of the undo stack. The file is not deleted."""
    retired = history / RETIRED_DIRNAME
    retired.mkdir(parents=True, exist_ok=True)
    target = retired / path.name
    n = 0
    while target.exists():
        n += 1
        target = retired / f"{path.stem}_{n}{path.suffix}"
    os.replace(path, target)


def _make_room_for_archive(history: Path) -> None:
    """Keep at most ``UNDO_STACK_LIMIT`` undoable logs once the new run lands.

    The new run becomes the current log, and the current log is about to
    join history. Room is made by retiring the oldest archived log, which
    stays on disk.
    """
    files = _numbered_history(history)
    keep = max(0, UNDO_STACK_LIMIT - 2)
    while len(files) > keep:
        _retire_file(history, files.pop(0))


def _archive_current_log(dest_dir: Path) -> None:
    current = log_path_for(dest_dir)
    if not current.is_file():
        return
    history = history_dir_for(dest_dir)
    history.mkdir(parents=True, exist_ok=True)
    _make_room_for_archive(history)
    os.replace(current, _next_history_path(history))


def _find_saved_path(log: RunLog, dest_dir: Path) -> Optional[Path]:
    key = _stable_key(log)
    for path in _log_files(dest_dir, include_retired=True):
        existing = _read_log(path)
        if existing is not None and _stable_key(existing) == key:
            return path
    return None


def save_log(log: RunLog, dest_dir: Path | str) -> Path:
    """Persist `log` without dropping a different run's log.

    An update (undo marking the same run) is written back to the file that
    already holds it. A new run archives the current log first, then becomes
    ``operation_log.json``.
    """
    dest_dir = Path(dest_dir)
    existing = _find_saved_path(log, dest_dir)
    if existing is not None:
        _write_log(log, existing)
        return existing
    if log_path_for(dest_dir).exists():
        _archive_current_log(dest_dir)
    path = log_path_for(dest_dir)
    _write_log(log, path)
    return path


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
    # Counted only after this run is fully undone, so a run that had to
    # leave files in place does not claim the older logs are next.
    if result.get("closed"):
        remaining = int(result.get("remaining") or 0)
        if remaining == 1:
            msg += "\n\n1 other run can still be undone, newest first."
        elif remaining > 1:
            msg += (
                f"\n\n{remaining} other runs can still be undone, newest first."
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
    key = _stable_key(log)
    remaining = sum(
        1 for item in list_run_logs(dest_dir)
        if not item.undone and _stable_key(item) != key
    )
    return {
        "undone": undone,
        "skipped": skipped,
        "failed": failed,
        "kept": kept,
        "remaining": remaining,
        "closed": log.undone,
    }
