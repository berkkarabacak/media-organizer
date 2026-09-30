"""Execute an organise plan: copy/move files, never overwriting, with a log.

Safety features:
- dry_run: full pipeline simulation, writes nothing
- crash journal: every completed file is journaled (JSONL) so an interrupted
  run can be resumed. A new organize replaces a finished journal; a resume
  appends to the unfinished one. Copies are fsynced to a temp name, then
  atomically renamed
- free-space preflight helper
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable, Optional

from .journal import JournalWriter, atomic_copy, atomic_move, cleanup_stale_parts
from .metadata import DateSource
from .organizer import OrganizeOptions, PlannedFile
from .plan import Operation, RunLog, new_log, save_log

#: warn when the copy would consume all but this fraction of free space
TIGHT_MARGIN = 0.05

# "Q:/photos" is a drive on Windows. On other systems it is a relative path,
# and walking up from it lands in the current directory.
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _unknown_free_space(needed_bytes: int, drive: str) -> dict:
    """Preflight could not read a disk. ``ok`` means do not block the run."""
    return {
        "free": -1,
        "needed": needed_bytes,
        "ok": True,
        "tight": False,
        "drive": drive,
        "unknown": True,
    }


def _final_destination(dest: Path) -> Path:
    """Double-check collision safety at execution time."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 0
    candidate = dest
    while candidate.exists():
        n += 1
        candidate = dest.with_name(f"{stem}_{n}{suffix}")
    return candidate


def free_space_status(dest_dir: Path | str, needed_bytes: int) -> dict:
    """Free-space preflight for the destination drive.

    Returns {free, needed, ok, tight, drive, unknown}. `ok` False means the
    copy cannot fit; `tight` means it fits with less than 5% margin left.
    `unknown` means the disk could not be read. `ok` is then true so this
    check does not block the run, and `free` is -1.
    """
    raw = os.fspath(dest_dir)
    if os.name != "nt" and _WINDOWS_DRIVE.match(raw.replace("\\", "/")):
        # Do not climb into the current directory and report its free space.
        return _unknown_free_space(needed_bytes, raw[:2] + "\\")
    p = Path(dest_dir)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        usage = shutil.disk_usage(p)
    except OSError:
        # drive missing/unreadable (e.g. unplugged): can't preflight — let the
        # executor's per-file error handling report it instead of crashing
        return _unknown_free_space(needed_bytes, p.anchor or str(p))
    free = usage.free
    return {
        "free": free,
        "needed": needed_bytes,
        "ok": needed_bytes <= free,
        "tight": needed_bytes > free * (1 - TIGHT_MARGIN),
        "drive": p.anchor or str(p),
        "unknown": False,
    }


def execute_plan(
    plan: list[PlannedFile],
    options: OrganizeOptions,
    progress: Optional[Callable[[int, int, str, int, int], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> tuple[RunLog, dict]:
    """Execute the plan. Returns (run log, summary counters).

    progress(i, total, current_name, bytes_done, total_bytes) is called before
    each item and once more at the end with i == total.
    """
    dry_run = getattr(options, "dry_run", False)
    log = new_log(options.dest_dir)
    summary = {"copied": 0, "moved": 0, "skipped_duplicates": 0,
               "undated": 0, "uncertain": 0, "errors": 0, "cancelled": False,
               "dry_run": dry_run,
               "action": "copy" if options.copy_mode else "move",
               "uncertain_aside": getattr(options, "uncertain", "aside") == "aside"}
    total = len(plan)
    total_bytes = sum(item.size for item in plan if not item.is_duplicate
                      and item.destination is not None and not item.error)
    bytes_done = 0

    journal: Optional[JournalWriter] = None
    if not dry_run:
        cleanup_stale_parts(options.dest_dir)
        # Replaces a finished journal. Continues one that is still open, which
        # is how resume keeps the files already organized in this run.
        journal = JournalWriter(options.dest_dir)

    # complete() only after the plan loop finishes with no break and no
    # unexpected exception. Cancel breaks out and must stay resumable.
    # Per-file OSError is caught inside the loop and is not this case.
    # Anything else (a bug, MemoryError, a runtime failure) used to reach
    # finally with cancelled still false and write {"run": "complete"}, so
    # the next launch treated the crash as finished and could replace the
    # journal. close() always runs, including when complete() is skipped.
    finished_normally = False
    try:
        for i, item in enumerate(plan):
            if cancel and cancel():
                summary["cancelled"] = True
                break
            if progress:
                progress(i, total, item.source.name, bytes_done, total_bytes)

            if item.is_duplicate:
                summary["skipped_duplicates"] += 1
                continue
            if item.destination is None or item.error:
                summary["errors"] += 1
                continue
            if not item.capture.found:
                summary["undated"] += 1
            elif item.capture.source is DateSource.MTIME:
                # Readable files almost always have an mtime, so "no date"
                # is rare. The default is to file these in _uncertain.
                summary["uncertain"] += 1

            action = "copy" if options.copy_mode else "move"
            op = Operation(action=action, source=str(item.source),
                           destination=str(item.destination))
            try:
                final = _final_destination(item.destination)
                op.destination = str(final)
                if dry_run:
                    # simulate: no filesystem writes at all
                    op.status = "done"
                    summary["copied" if options.copy_mode else "moved"] += 1
                    bytes_done += item.size
                else:
                    final.parent.mkdir(parents=True, exist_ok=True)
                    if options.copy_mode:
                        digest = atomic_copy(item.source, final)
                        summary["copied"] += 1
                    else:
                        digest = atomic_move(item.source, final)
                        summary["moved"] += 1
                    op.sha256 = digest
                    try:
                        op.size = final.stat().st_size
                    except OSError:
                        op.size = item.size
                    op.status = "done"
                    bytes_done += item.size
                    journal.record(action, str(item.source), str(final))
            except OSError as exc:
                op.status = "error"
                op.error = str(exc)
                summary["errors"] += 1
            log.operations.append(op)
        else:
            # No break (cancel) and no exception escaped the loop.
            finished_normally = True
    finally:
        if journal is not None:
            try:
                if finished_normally and not summary["cancelled"]:
                    journal.complete()
            finally:
                journal.close()

    if not dry_run:
        save_log(log, options.dest_dir)
    if progress:
        progress(total, total, "", total_bytes, total_bytes)
    return log, summary
