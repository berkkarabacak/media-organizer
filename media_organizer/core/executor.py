"""Execute an organise plan: copy/move files, never overwriting, with a log."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from .organizer import OrganizeOptions, PlannedFile
from .plan import Operation, RunLog, new_log, save_log


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


def execute_plan(
    plan: list[PlannedFile],
    options: OrganizeOptions,
    progress: Optional[Callable[[int, int, str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> tuple[RunLog, dict]:
    """Execute the plan. Returns (run log, summary counters)."""
    log = new_log(options.dest_dir)
    summary = {"copied": 0, "moved": 0, "skipped_duplicates": 0,
               "undated": 0, "errors": 0, "cancelled": False}
    total = len(plan)

    for i, item in enumerate(plan):
        if cancel and cancel():
            summary["cancelled"] = True
            break
        if progress:
            progress(i, total, item.source.name)

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
            final.parent.mkdir(parents=True, exist_ok=True)
            op.destination = str(final)
            if options.copy_mode:
                shutil.copy2(item.source, final)
                summary["copied"] += 1
            else:
                shutil.move(str(item.source), str(final))
                summary["moved"] += 1
            op.status = "done"
        except OSError as exc:
            op.status = "error"
            op.error = str(exc)
            summary["errors"] += 1
        log.operations.append(op)

    save_log(log, options.dest_dir)
    if progress:
        progress(total, total, "")
    return log, summary
