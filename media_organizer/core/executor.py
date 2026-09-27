"""Execute an organise plan: copy/move files, never overwriting, with a log.

Safety features:
- dry_run: full pipeline simulation, writes nothing
- crash journal: every completed file is journaled (JSONL) so an interrupted
  run can be resumed; copies go through temp-name + atomic rename
- free-space preflight helper
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from .journal import JournalWriter, atomic_copy, atomic_move, cleanup_stale_parts
from .organizer import OrganizeOptions, PlannedFile
from .plan import Operation, RunLog, new_log, save_log

#: warn when the copy would consume all but this fraction of free space
TIGHT_MARGIN = 0.05


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

    Returns {free, needed, ok, tight, drive}. `ok` False means the copy
    cannot fit; `tight` means it fits with less than 5% margin left.
    """
    p = Path(dest_dir)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        usage = shutil.disk_usage(p)
    except OSError:
        # drive missing/unreadable (e.g. unplugged): can't preflight — let the
        # executor's per-file error handling report it instead of crashing
        return {"free": -1, "needed": needed_bytes, "ok": True,
                "tight": False, "drive": p.anchor or str(p), "unknown": True}
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
               "undated": 0, "errors": 0, "cancelled": False,
               "dry_run": dry_run}
    total = len(plan)
    total_bytes = sum(item.size for item in plan if not item.is_duplicate
                      and item.destination is not None and not item.error)
    bytes_done = 0

    journal: Optional[JournalWriter] = None
    if not dry_run:
        cleanup_stale_parts(options.dest_dir)
        journal = JournalWriter(options.dest_dir)

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
                        atomic_copy(item.source, final)
                        summary["copied"] += 1
                    else:
                        atomic_move(item.source, final)
                        summary["moved"] += 1
                    op.status = "done"
                    bytes_done += item.size
                    journal.record(action, str(item.source), str(final))
            except OSError as exc:
                op.status = "error"
                op.error = str(exc)
                summary["errors"] += 1
            log.operations.append(op)
    finally:
        if journal is not None:
            if not summary["cancelled"]:
                journal.complete()
            journal.close()

    if not dry_run:
        save_log(log, options.dest_dir)
    if progress:
        progress(total, total, "", total_bytes, total_bytes)
    return log, summary
