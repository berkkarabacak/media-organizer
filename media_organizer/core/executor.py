"""Execute an organise plan: copy/move files, never overwriting, with a log.

Safety features:
- dry_run: full pipeline simulation, writes nothing
- crash journal: every completed file is journaled (JSONL) so an interrupted
  run can be resumed. A new organize replaces a finished journal; a resume
  appends to the unfinished one. Per-file errors leave the journal
  unfinished so Resume can skip the files already recorded. Copies are
  fsynced to a temp name, then atomically renamed. A cross-volume move
  journals that copy before the source is removed. A same-volume move
  hashes the source before renaming it; a journal failure after that
  rename keeps the row done so Undo can put the file back, and still
  counts an error so the run stays resumable. Resume still removes
  a cross-volume source when the done line is already there and the
  destination bytes still match; a missing or different destination is
  left alone. A destination that already holds this source's bytes is
  journaled and not copied again. A different file at that path still
  gets a collision suffix.
- free-space preflight helper. The byte total is what this run will
  still write, not rows Resume will skip and not a destination that
  already holds the source. A same-volume move is a rename and is not
  counted. A copy, a cross-volume move, and a move whose device cannot
  be read still count in full.
- operation log: saved on success, on cancel once an operation was
  recorded, and when an unexpected exception aborts the loop after at
  least one operation was recorded. A cancel before any recorded
  operation does not publish an empty log, and it does not leave an
  unfinished journal that has zero done lines.
  Resume seeds that log from the unfinished journal first, so undo
  covers files journaled before a crash that never reached save_log.
  The seeded sha256 and size are the ones stored on the journal line,
  not a hash of whatever file is at the destination now. A legacy move
  line that omitted both still gets that pair's hash when the source
  and destination are two regular files with the same bytes and this
  run is about to remove the source. That hash is fsynced onto the
  existing journal line before the source is unlinked, so a kill before
  ``save_log`` does not drop it. A second done line is not appended.
  A missing side, or two files that differ, is not hashed. A move with
  no recorded sha256 is not undone. The log is fsynced before its name
  is published.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable, Optional

from .journal import (JournalWriter, atomic_copy, atomic_move,
                       cleanup_stale_parts, completed_operations,
                       discard_journal, file_sha256, files_identical,
                       find_unfinished_journal, path_identity)
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


def _same_file(src: Path, dest: Path) -> bool:
    """True when both paths are the same directory entry."""
    try:
        return src.samefile(dest)
    except OSError:
        return False


def _existing_ancestor(path: Path | str) -> Path | None:
    """Closest existing path, climbing parents that are not created yet.

    ``None`` when ``path`` is a Windows drive letter seen on another
    system. That string is relative there, and climbing it would use the
    current directory.
    """
    raw = os.fspath(path)
    if os.name != "nt" and _WINDOWS_DRIVE.match(raw.replace("\\", "/")):
        return None
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _volume_id(path: Path | str) -> int | None:
    """``st_dev`` of ``path``, or of the closest existing parent.

    ``None`` when the device cannot be read. Move preflight then counts
    the file: undercounting can start Organize and run out of space.
    ``Path.exists`` re-raises errors other than "not found", so the climb
    is inside the same guard.
    """
    try:
        anchor = _existing_ancestor(path)
        if anchor is None:
            return None
        return anchor.stat().st_dev
    except OSError:
        return None


def bytes_still_needed(
    plan,
    *,
    copy_mode: bool = True,
    dest_dir: Path | str | None = None,
) -> int:
    """Bytes ``execute_plan`` will still write for ``plan``.

    Duplicates and rows with no destination are omitted, same as the
    organize dialog's previous sum. A destination that already holds this
    source's bytes is omitted too: the executor journals that row and does
    not copy or move new bytes onto the drive.

    Copy mode counts every other row in full, including one whose final
    name is already a different file and will be written beside it under a
    collision suffix.

    Move mode (``copy_mode=False``) omits a row whose source is on the
    same device as ``dest_dir``. ``atomic_move`` renames that file with
    ``os.replace`` and does not need a second copy of those bytes. A
    different device still needs the durable copy before the source is
    unlinked, so that size counts. If either device cannot be read, or
    ``dest_dir`` was not passed, the size counts too.
    """
    total = 0
    dest_dev = None
    if not copy_mode and dest_dir is not None:
        dest_dev = _volume_id(dest_dir)
    for item in plan:
        if item.is_duplicate or not item.destination:
            continue
        if files_identical(item.source, item.destination):
            continue
        if (not copy_mode and dest_dev is not None
                and _volume_id(item.source) == dest_dev):
            continue
        total += item.size
    return total


def free_space_status(dest_dir: Path | str, needed_bytes: int) -> dict:
    """Free-space preflight for the destination drive.

    Returns {free, needed, ok, tight, drive, unknown}. `ok` False means the
    copy cannot fit; `tight` means it fits with less than 5% margin left.
    `unknown` means the disk could not be read. `ok` is then true so this
    check does not block the run, and `free` is -1.
    """
    anchor = _existing_ancestor(dest_dir)
    if anchor is None:
        # Do not climb into the current directory and report its free space.
        raw = os.fspath(dest_dir)
        return _unknown_free_space(needed_bytes, raw[:2] + "\\")
    try:
        usage = shutil.disk_usage(anchor)
    except OSError:
        # drive missing/unreadable (e.g. unplugged): can't preflight — let the
        # executor's per-file error handling report it instead of crashing
        return _unknown_free_space(needed_bytes, anchor.anchor or str(anchor))
    free = usage.free
    return {
        "free": free,
        "needed": needed_bytes,
        "ok": needed_bytes <= free,
        "tight": needed_bytes > free * (1 - TIGHT_MARGIN),
        "drive": anchor.anchor or str(anchor),
        "unknown": False,
    }


def _record_legacy_move_pair(log: RunLog | None, source: Path,
                             destination: Path,
                             writer: JournalWriter | None = None) -> bool:
    """Store sha256 and size for a legacy move that omitted both.

    Called only when ``source`` and ``destination`` are two different
    regular files and ``files_identical`` just said they match, and
    only because this run is about to remove ``source``. The hash is
    those shared bytes. It is not taken from a destination whose
    source is already gone, and a row that already recorded a sha256
    or a size is left as the journal wrote it.

    The hash is fsynced onto the existing crash-journal line before
    the seeded row is updated and before this returns True. ``save_log``
    runs only at the end of the plan. A kill after the source is gone
    and before that save still has to find the hash in the journal.
    The line is rewritten in place. Appending a second done line would
    seed two undo rows, and Undo would park the destination beside the
    source when that source was still there.

    Returns False when the journal line cannot be updated, so the
    caller leaves the source in place. Returns True when the hash is
    durable. A row that already recorded a sha256 or a size is not
    changed; this function is not called for that row.
    """
    if writer is None:
        return False
    try:
        digest = file_sha256(destination)
        size = destination.stat().st_size
    except OSError:
        return False
    if not writer.stamp_legacy_move(
            str(source), str(destination), digest, size):
        return False
    if log is None:
        return True
    source_key = path_identity(source)
    dest_key = path_identity(destination)
    for op in log.operations:
        if op.action != "move" or op.status != "done" or not op.destination:
            continue
        if op.sha256 or op.size >= 0:
            continue
        if path_identity(op.source) != source_key:
            continue
        if path_identity(op.destination) != dest_key:
            continue
        op.sha256 = digest
        op.size = size
    return True


def finish_pending_move_unlinks(dest_dir: Path | str, plan=(), *,
                                copy_mode: bool = False,
                                log: RunLog | None = None,
                                writer: JournalWriter | None = None) -> int:
    """Remove sources a cross-volume move journaled but did not unlink.

    Returns how many sources were removed.

    The done line is written before ``source.unlink()`` so a crash cannot
    drop the only copy. Resume then drops that source from the plan, and
    the original file would stay in the library forever. On a Move resume,
    unlink it when the journal action is move, this ``plan`` will not move
    it itself, and the destination is still a byte-for-byte match of a
    different file.

    A journal line from before sha256 was stored has an empty hash.
    Undo cannot put the file back after this unlink unless that hash
    is recorded first. When both files are still present and identical,
    the pair's sha256 and size are fsynced onto that existing journal
    line, then copied onto the seeded row, and only then is the source
    removed. A row that already has either field is not rewritten.
    ``writer`` is the journal already opened for this run. Without it
    the hash would sit only in memory until ``save_log``.

    Leave the source alone when the destination is missing, the bytes
    differ, or the two paths are the same file. That source may be the
    only copy. Copy mode removes nothing. A journaled copy removes
    nothing even when this run is a move. Same-volume moves already
    dropped the source name inside ``os.replace`` before the done line,
    so there is nothing left to unlink.
    """
    if copy_mode:
        return 0
    journal_path = find_unfinished_journal(dest_dir)
    if journal_path is None:
        return 0
    # Rows still in the plan take the reused-destination path: journal,
    # then unlink. Removing the source first would make that path miss
    # the byte check and publish a collision copy.
    deferred: set[str] = set()
    for item in plan:
        if item.is_duplicate or not item.destination or item.error:
            continue
        deferred.add(path_identity(item.source))
    removed = 0
    for entry in completed_operations(journal_path):
        if entry["action"] != "move" or not entry["destination"]:
            continue
        source = Path(entry["source"])
        destination = Path(entry["destination"])
        if path_identity(source) in deferred:
            continue
        if path_identity(source) == path_identity(destination):
            continue
        if not files_identical(source, destination):
            continue
        if _same_file(source, destination):
            continue
        # Hash the pair that is about to lose its source. A later Undo
        # sees only the destination, so an empty legacy sha256 would
        # leave that only copy stuck there. The hash has to be durable
        # in the journal before the unlink: this run's save_log has not
        # happened yet. If it cannot be stored, keep the source.
        if (not entry["sha256"] and entry["size"] < 0
                and not _record_legacy_move_pair(
                    log, source, destination, writer)):
            continue
        try:
            source.unlink()
        except FileNotFoundError:
            continue
        removed += 1
    return removed


def saved_run_owns_plan_row(item, owned: set[str]) -> bool:
    """True when running ``item`` would re-journal a saved run's file.

    ``execute_plan`` journals a destination that already matches and does
    not copy it again. That done row is undoable, so Undo deletes the
    file. A saved run already recorded this path: the file belongs to
    that log. A missing source with the file still there is the same
    case after a move. A missing destination, or different bytes while
    the source is still on disk, is not owned for this check. Discard
    still has to write that file.
    """
    dest = getattr(item, "destination", None)
    if not dest or not owned:
        return False
    dest = Path(dest)
    if path_identity(dest) not in owned or not dest.is_file():
        return False
    source = Path(item.source)
    if files_identical(source, dest):
        return True
    return not source.exists()


def _move_source_conflicts(source: str, destination: str) -> bool:
    """True when a journaled move's source is a different file than dest.

    Both paths are regular files and their bytes differ. The destination
    is then not something undo should relocate: the source is still the
    user's file, or it was replaced after the interrupt. A missing source
    (same-volume rename) or a missing destination is not this case.
    """
    if not source or not destination:
        return False
    src, dest = Path(source), Path(destination)
    try:
        if not src.is_file() or not dest.is_file():
            return False
    except OSError:
        return False
    return not files_identical(src, dest)


def _seed_log_from_journal(log: RunLog, dest_dir: Path | str) -> None:
    """Append the open journal's done files to ``log`` before the plan runs.

    A crash journals each finished file and can die before ``save_log``.
    Resume skips those sources, so a new empty log would omit them and
    undo would leave them where the crashed run put them.

    Size and sha256 come from the journal line, written when the file was
    published. They are not read from whoever occupies the destination
    now. A line that omitted them (an older journal, or a destination
    that was never hashed) stays ``size=-1`` and an empty sha256 here.
    Move undo refuses that row instead of trusting the path. A Move
    resume that then removes a still-matching source fsyncs the pair's
    hash onto the journal line and the seeded row before the unlink.
    This function does not.

    A move whose source is still a file and does not match the destination
    is recorded as ``kept``. Undo must not relocate that occupant back
    onto the source or beside it.
    """
    journal = find_unfinished_journal(dest_dir)
    if journal is None:
        return
    for entry in completed_operations(journal):
        destination = entry["destination"]
        action = entry["action"]
        status = "done"
        if action == "move" and _move_source_conflicts(
                entry["source"], destination):
            status = "kept"
        log.operations.append(Operation(
            action=action,
            source=entry["source"],
            destination=destination,
            status=status,
            size=entry.get("size", -1),
            sha256=entry.get("sha256") or "",
        ))


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
        # Delete leftover .mediaorganizer.part files. Do not rename an
        # orphan part onto the final name: a crash before fsync can leave
        # it truncated, and the source is still present until after the
        # journal line. Promoting it would publish junk, then resume would
        # write a collision copy and a move could unlink the source.
        cleanup_stale_parts(options.dest_dir)
        # Read the open run before JournalWriter opens it. A finished
        # journal is truncated on open; an unfinished one is appended to.
        # Seeding first is how undo covers files journaled before a crash
        # that never reached save_log. Dry runs write no log and skip this.
        _seed_log_from_journal(log, options.dest_dir)
        # Replaces a finished journal. Continues one that is still open, which
        # is how resume keeps the files already organized in this run.
        journal = JournalWriter(options.dest_dir)

    # complete() only when the plan loop finishes with no break, no
    # unexpected exception, and no per-file error. Cancel breaks out and
    # must stay resumable. A per-file OSError (copy, move, or journal) and
    # a plan row with ``item.error`` or no destination are caught inside
    # the loop and counted in ``summary["errors"]``. Those used to take
    # this path and write {"run": "complete"} anyway. Resume then had no
    # unfinished journal, so the next Copy treated destinations that
    # already held the successful files as collisions (``photo_1.jpg``).
    # A publish that landed before ``JournalWriter.record`` failed was
    # also closed, so the identical-destination reuse path could not
    # adopt it. Leaving the journal open keeps the done lines that were
    # recorded; Resume skips those sources and retries the rest.
    # A same-volume rename has already dropped the source name by the
    # time record runs. That row stays ``done`` when record fails: Undo
    # only reverses ``done``, and the bytes are only at the destination.
    # The error is still counted, so ``complete()`` is skipped.
    # Anything else (a bug, MemoryError, a runtime failure) used to reach
    # finally with cancelled still false and write {"run": "complete"}, so
    # the next launch treated the crash as finished and could replace the
    # journal. close() always runs, including when complete() is skipped.
    # That failure also used to skip save_log. The journal could resume,
    # but undo had no record of the files already written. save_log runs
    # for it when the log has any operation (including ones seeded from
    # the open journal). The journal stays unfinished.
    finished_normally = False
    aborted: Optional[Exception] = None
    try:
        # Journaled cross-volume moves are not in ``plan`` (Resume excludes
        # done sources). Finish the unlink now, before those rows are
        # treated as fully settled. Copy mode and a destination that no
        # longer matches leave the source in place.
        if journal is not None and not options.copy_mode:
            finish_pending_move_unlinks(
                options.dest_dir, plan, copy_mode=False, log=log,
                writer=journal)
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
                # Published already, and still this source: do not copy a
                # second file to name_1. A different occupant falls through
                # to the collision suffix. The match is journaled below so
                # Undo can remove it. Discard must not pass a row a saved
                # run already owns: this branch would put that file in the
                # new log, and Undo would delete it.
                reused = files_identical(item.source, item.destination)
                if reused:
                    final = item.destination
                else:
                    final = _final_destination(item.destination)
                op.destination = str(final)
                if dry_run:
                    # Simulate: no filesystem writes at all. Leave size and
                    # sha256 unset. Undo refuses an unsaved log whose done
                    # rows look like this, so it cannot delete a file the
                    # dry run did not write.
                    op.status = "done"
                    summary["copied" if options.copy_mode else "moved"] += 1
                    bytes_done += item.size
                else:
                    final.parent.mkdir(parents=True, exist_ok=True)
                    # Copy keeps the source. Same-volume move hashes the
                    # source, then renames it away inside atomic_move.
                    # Cross-volume move copies and leaves the source until
                    # the journal line below is durable; only then is the
                    # source unlinked. A file that is already at ``final``
                    # takes the same order: journal, then unlink, and only
                    # when it is not the same file.
                    unlink_source_after_journal = False
                    if reused:
                        digest = file_sha256(final)
                        summary["copied" if options.copy_mode else "moved"] += 1
                        if (not options.copy_mode
                                and not _same_file(item.source, final)):
                            unlink_source_after_journal = True
                    elif options.copy_mode:
                        digest = atomic_copy(item.source, final)
                        summary["copied"] += 1
                    else:
                        digest, unlink_source_after_journal = atomic_move(
                            item.source, final)
                        summary["moved"] += 1
                    op.sha256 = digest
                    try:
                        op.size = final.stat().st_size
                    except OSError:
                        op.size = item.size
                    op.status = "done"
                    bytes_done += item.size
                    journal.record(
                        action, str(item.source), str(final),
                        sha256=op.sha256, size=op.size)
                    if unlink_source_after_journal:
                        try:
                            item.source.unlink()
                        except FileNotFoundError:
                            pass
            except OSError as exc:
                # Same-volume rename already published the file and
                # removed the source. sha256 was taken before that
                # rename. Marking the row error would make Undo skip it
                # for good. Keep done, count the error, and try once
                # more to journal the line. Copy and cross-volume still
                # have the source, so a failed record stays an error and
                # Resume can adopt or retry it.
                dest_path = Path(op.destination) if op.destination else None
                published_move = (
                    op.action == "move"
                    and bool(op.sha256)
                    and dest_path is not None
                    and not Path(op.source).exists()
                    and dest_path.is_file()
                )
                if published_move:
                    op.status = "done"
                    if journal is not None:
                        try:
                            journal.record(
                                op.action, op.source, op.destination,
                                sha256=op.sha256, size=op.size)
                        except OSError:
                            pass
                else:
                    op.status = "error"
                op.error = str(exc)
                summary["errors"] += 1
            log.operations.append(op)
        else:
            # No break (cancel) and no exception escaped the loop.
            finished_normally = True
    except Exception as exc:
        aborted = exc
    finally:
        if journal is not None:
            try:
                if (finished_normally and aborted is None
                        and not summary["cancelled"]
                        and summary["errors"] == 0):
                    journal.complete()
            finally:
                journal.close()

    # A cancel before any operation was recorded must not publish an empty
    # undo log or leave an unfinished journal with zero done lines.
    # Discard already saved the seeded journal and dropped it, so this
    # restart has nothing to seed. JournalWriter still opens a new file,
    # and saving would archive that seeded log behind a run that restores
    # nothing. The next Organize would offer Resume for 0 files.
    # Cancel after a recorded file, a normal finish, and an unexpected
    # exception that already recorded work still persist. A dry run never
    # writes a log. An unexpected exception with an empty log does not.
    if not dry_run and summary["cancelled"] and not log.operations:
        open_journal = find_unfinished_journal(options.dest_dir)
        if (open_journal is not None
                and not completed_operations(open_journal)):
            discard_journal(options.dest_dir)
    elif not dry_run and (aborted is None or log.operations):
        save_log(log, options.dest_dir)
    if aborted is not None:
        raise aborted
    if progress:
        progress(total, total, "", total_bytes, total_bytes)
    return log, summary
