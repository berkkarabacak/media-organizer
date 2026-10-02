"""Crash-resilience journal: one JSONL file per destination.

Each completed file operation is journaled immediately, so a power cut or
crash mid-run leaves a resumable record. A new organize replaces that file.
Resuming an interrupted run appends to the same file until it is marked
complete. Copies are flushed and fsynced to a temp name, then atomically
renamed, so a half-written file never appears at its final name and a
journal "done" line is not written for bytes still only in the page cache.
A leftover ``.mediaorganizer.part`` is deleted on the next run. It is
not renamed onto the final name: the part may be truncated, and the
source is still present until after the journal line.
Pure logic, no Qt.
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import ntpath
import os
import shutil
from pathlib import Path
from typing import Iterable, Iterator, Optional

JOURNAL_DIRNAME = ".mediaorganizer-journal"
JOURNAL_FILENAME = "operations.jsonl"
# Distinct from a user file that merely ends in ".part". Incomplete copies
# are named "<final name>.mediaorganizer.part". Cleanup deletes that file
# whether or not the final name is already there. Promoting it would
# publish a possibly truncated copy under the real filename. The source
# is still on disk until the journal line, which is written only after
# the final name exists.
PART_SUFFIX = ".mediaorganizer.part"


def journal_path_for(dest_dir: Path | str) -> Path:
    return Path(dest_dir) / JOURNAL_DIRNAME / JOURNAL_FILENAME


class JournalWriter:
    """JSONL journal for one organize run in the destination.

    A new run truncates ``operations.jsonl``. An interrupted run is continued
    by appending; ``complete()`` is what closes it. Pass ``resume`` to force
    either mode. When it is omitted, an existing unfinished journal is
    continued and anything else (missing file, or a run that already
    completed) is replaced.

    ``record`` stores the sha256 and size of the bytes just published.
    Resume seeds undo from those fields. It does not hash whatever file
    is at the destination later. A legacy move line that omitted both
    can be filled in place, before the source is removed, so a crash
    before the operation log is saved still has a hash to seed. That
    update does not append a second done line.
    """

    def __init__(self, dest_dir: Path | str, *, resume: bool | None = None):
        self.path = journal_path_for(dest_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if resume is None:
            resume = find_unfinished_journal(dest_dir) is not None
        # "w" drops a finished journal so a later interrupt is not hidden by
        # an older complete marker. "a" keeps the open run's records.
        self._fh = open(self.path, "a" if resume else "w", encoding="utf-8")

    def record(self, action: str, source: str, destination: str,
               sha256: str = "", size: int = -1) -> None:
        """Append one done line and fsync it.

        ``sha256`` and ``size`` are the bytes at the destination when this
        line is written. Pass them from the copy or move that just
        published the file. Older journals omit both; resume must not
        invent them from a later occupant of the path.
        """
        entry = {
            "action": action,
            "source": source,
            "destination": destination,
            "status": "done",
        }
        if sha256:
            entry["sha256"] = sha256
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
            entry["size"] = size
        self._fh.write(json.dumps(entry) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def complete(self) -> None:
        self._fh.write(json.dumps({"run": "complete"}) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        fh = self._fh
        if fh is not None and not fh.closed:
            fh.close()

    def stamp_legacy_move(self, source: str, destination: str,
                          sha256: str, size: int) -> bool:
        """Fsync sha256 and size onto each matching legacy move line.

        The line is a done move in the open run that names this pair and
        omitted both fields. Other lines stay as they were, including a
        line that already recorded a hash or a size. Nothing is appended:
        a second done line would seed a second undo row.

        The append handle is closed for the replace, then reopened at the
        end of the new file so a later ``record`` or ``complete`` still
        appends. Returns False when the line cannot be updated. The
        caller must not remove the source in that case.
        """
        if (not sha256 or isinstance(size, bool) or not isinstance(size, int)
                or size < 0):
            return False
        self._fh.flush()
        os.fsync(self._fh.fileno())
        try:
            original = self.path.read_text(encoding="utf-8")
        except OSError:
            return False
        rewritten = _stamp_legacy_move_text(
            original, source, destination, sha256, size)
        if rewritten is None:
            return False
        # Same publish order as save_log: fsync the temp file, then
        # replace. The append handle has to be closed first. On Windows
        # a replace of a file this process still has open fails, and on
        # Linux a later write would hit the old inode.
        tmp = self.path.with_name(self.path.name + ".stamp")
        self._fh.close()
        replaced = False
        try:
            with open(tmp, "w", encoding="utf-8") as out:
                out.write(rewritten)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.path)
            replaced = True
        except OSError:
            replaced = False
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            self._fh = open(self.path, "a", encoding="utf-8")
        return replaced

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _journal_entries(path: Path) -> Iterator[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue  # tolerate a torn final line after a crash
    except OSError:
        return


def _open_run(path: Path) -> tuple[list[dict], bool]:
    """Return ``(entries after the last complete marker, run is finished)``.

    Only that tail is the current run. An earlier ``{"run": "complete"}``
    closes the previous segment and does not finish a later one. A file with
    no parsed records is unfinished: the writer was opened and the run
    stopped before a complete marker (cancel, or a crash on the first line).
    """
    current: list[dict] = []
    finished = False
    saw = False
    for entry in _journal_entries(path):
        saw = True
        if entry.get("run") == "complete":
            current = []
            finished = True
        else:
            current.append(entry)
            finished = False
    if not saw:
        return [], False
    return current, finished


def find_unfinished_journal(dest_dir: Path | str) -> Optional[Path]:
    """Return the journal path if the latest run was interrupted.

    A destination can be organized more than once. A complete marker from an
    earlier run does not hide a later run that stopped before ``complete()``.
    """
    path = journal_path_for(dest_dir)
    if not path.exists():
        return None
    _entries, finished = _open_run(path)
    return None if finished else path


def path_identity(path: str | os.PathLike, *, windows: bool | None = None) -> str:
    """Stable key for one file across resume scans.

    The journal keeps the original path text. Resume compares keys so an
    older ``str(Path)`` entry still matches a later scan.

    On Windows, and when ``windows=True``, the key is
    ``normcase(normpath(abspath(...)))`` via ``ntpath``: drive and folder
    case do not matter, and ``/`` and ``\\`` are the same separator. That
    is what ``os.path`` does on Windows. ``windows=True`` forces the same
    key on Linux so the resume regression does not need a Windows runner.

    On POSIX the key is ``normpath(abspath(...))`` and case is preserved.
    """
    text = os.fspath(path)
    if windows is None:
        windows = os.name == "nt"
    if windows:
        return ntpath.normcase(ntpath.normpath(ntpath.abspath(text)))
    return os.path.normpath(os.path.abspath(text))


def _entry_sha256(entry: dict) -> str:
    digest = entry.get("sha256") or ""
    return digest if isinstance(digest, str) else ""


def _entry_size(entry: dict) -> int:
    size = entry.get("size", -1)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return -1
    return size


def _parse_journal_line(line: str) -> dict | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        entry = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict):
        return None
    return entry


def _stamp_legacy_move_text(text: str, source: str, destination: str,
                            sha256: str, size: int) -> str | None:
    """Return journal text with this legacy move line filled in.

    ``None`` when no open-run done move for this pair omitted both
    sha256 and size. Lines before the last complete marker, torn lines,
    and every other done line are copied through unchanged.
    """
    source_key = path_identity(source)
    dest_key = path_identity(destination)
    lines = text.splitlines(keepends=True)
    parsed: list[dict | None] = []
    last_complete = -1
    for index, line in enumerate(lines):
        entry = _parse_journal_line(line)
        parsed.append(entry)
        if entry is not None and entry.get("run") == "complete":
            last_complete = index
    changed = False
    for index, entry in enumerate(parsed):
        if index <= last_complete or entry is None:
            continue
        if entry.get("status") != "done" or entry.get("action") != "move":
            continue
        src = entry.get("source") or ""
        dest = entry.get("destination") or ""
        if not isinstance(src, str) or not isinstance(dest, str):
            continue
        if not src or not dest:
            continue
        if path_identity(src) != source_key or path_identity(dest) != dest_key:
            continue
        if _entry_sha256(entry) or _entry_size(entry) >= 0:
            continue
        entry["sha256"] = sha256
        entry["size"] = size
        newline = ""
        raw = lines[index]
        if raw.endswith("\r\n"):
            newline = "\r\n"
        elif raw.endswith("\n"):
            newline = "\n"
        lines[index] = json.dumps(entry) + newline
        changed = True
    if not changed:
        return None
    return "".join(lines)


def completed_operations(journal_path: Path | str) -> list[dict]:
    """Done operations of the open run, in journal order.

    Each item has the ``action``, ``source``, and ``destination`` stored
    in the journal, plus ``sha256`` and ``size`` when that line recorded
    them. Missing integrity fields stay ``""`` and ``-1``. They are not
    filled from the destination path. A run that already wrote
    ``{"run": "complete"}`` contributes nothing: those files belong to a
    finished organize. Resume seeds the operation log from this list so
    undo still covers files that were journaled before ``save_log`` ran.
    """
    entries, _finished = _open_run(Path(journal_path))
    ops: list[dict] = []
    for entry in entries:
        source = entry.get("source")
        if entry.get("status") != "done" or not source:
            continue
        ops.append({
            "action": entry.get("action") or "",
            "source": source,
            "destination": entry.get("destination") or "",
            "sha256": _entry_sha256(entry),
            "size": _entry_size(entry),
        })
    return ops


def completed_sources(journal_path: Path | str) -> set[str]:
    """Sources fully written in the interrupted run (safe to skip on resume).

    Values are the path strings stored in the journal, not identity keys.
    Compare them with :func:`path_identity` (or
    :func:`exclude_completed_sources`) so slash style and, on Windows,
    case still match. Sources from a run that already wrote
    ``{"run": "complete"}`` are not included. Those files belong to a
    finished organize, not this resume.
    """
    return {op["source"] for op in completed_operations(journal_path)}


def completed_destinations(journal_path: Path | str) -> set[str]:
    """Destinations fully written in the interrupted run.

    Values are the original journal strings. Compare with
    :func:`path_identity`, the same way resume matches sources.
    """
    entries, _finished = _open_run(Path(journal_path))
    return {e["destination"] for e in entries
            if e.get("status") == "done" and e.get("destination")}


def exclude_completed_sources(plan, completed: Iterable[str | os.PathLike], *,
                              windows: bool | None = None) -> list:
    """Plan rows whose source is not already in ``completed``.

    Both the journal strings and each plan source go through
    :func:`path_identity`. ``C:/Photos/A.jpg`` and ``c:\\photos\\a.jpg``
    are the same file on Windows, including for a journal written before
    this helper existed. A different file is kept.

    A journaled cross-volume move can still have its source on disk: the
    done line is written before the unlink. This helper still drops that
    row so the bytes are not copied again. ``execute_plan`` removes the
    source when the destination is still a match.
    """
    done = {path_identity(source, windows=windows) for source in completed}
    return [item for item in plan
            if path_identity(item.source, windows=windows) not in done]


def discard_journal(dest_dir: Path | str) -> None:
    """Remove the journal (user chose 'Discard')."""
    path = journal_path_for(dest_dir)
    try:
        path.unlink()
    except OSError:
        pass
    try:
        path.parent.rmdir()  # remove dir only if empty
    except OSError:
        pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    """SHA-256 of the file's bytes."""
    return _sha256(path)


def files_identical(source: Path, dest: Path) -> bool:
    """True when ``dest`` is a byte-for-byte match of ``source``.

    Both paths must be regular files of the same size. The comparison reads
    the contents (``filecmp`` with ``shallow=False``), so a same-size file
    with different bytes is not a match. A missing path, a directory, or an
    unreadable file is not a match: callers then keep the never-overwrite
    collision suffix.
    """
    try:
        if not source.is_file() or not dest.is_file():
            return False
        if source.stat().st_size != dest.stat().st_size:
            return False
        return filecmp.cmp(source, dest, shallow=False)
    except OSError:
        return False


def atomic_copy(source: Path, final: Path) -> str:
    """Copy via temp name + atomic rename. Returns the sha256 of the bytes.

    A crash mid-copy leaves only a ``.mediaorganizer.part`` file, never a
    truncated file at the final name. That part is not promoted later.
    The source is still present (a move unlinks it only after the journal
    line, which is written after this rename), and a crash before fsync
    can leave the part short. ``cleanup_stale_parts`` deletes it.
    A stale part for this same destination is replaced by this copy;
    other ``*.part`` files are not touched.

    The part file is flushed and ``os.fsync``'d before ``os.replace``. The
    journal records the copy only after this returns, so a power cut cannot
    leave a durable "done" line for bytes that never reached disk. Resume
    would otherwise skip that source. A cross-volume move must not remove
    the source until that journal line is durable; ``atomic_move`` leaves
    the source in place so the caller can journal first.

    The parent directory is not fsynced after the rename. That call is not
    portable: on Windows ``os.open`` of a directory raises ``PermissionError``
    (WinError 5). The C runtime will not open a directory, and ``os.fsync``
    there is ``_commit`` / ``FlushFileBuffers``, which does not accept a
    directory handle. A Linux-only directory fsync would not cover the
    shipped app. Some FUSE and network filesystems also return ``EINVAL``
    for directory fsync; raising that after ``os.replace`` would report a
    copy that is already at its final name as a failure.
    """
    part = final.with_name(final.name + PART_SUFFIX)
    try:
        part.unlink()
    except OSError:
        pass
    digest = hashlib.sha256()
    with open(source, "rb") as src, open(part, "wb") as out:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
            out.write(chunk)
        # Flush the Python buffer, then the kernel cache, before the name
        # is published. close() alone only drops the user-space buffer.
        out.flush()
        os.fsync(out.fileno())
    shutil.copystat(source, part)
    os.replace(part, final)
    return digest.hexdigest()


def atomic_move(source: Path, final: Path) -> tuple[str, bool]:
    """Move with crash safety.

    Returns ``(sha256, unlink_source_after_journal)``.

    Same-volume renames are atomic: ``os.replace`` places the file at
    ``final`` and removes the source name in one step. The flag is false.
    The caller still journals after this returns.

    Across volumes ``os.replace`` fails. The file is copied durably
    (``atomic_copy`` fsyncs the part file before rename) and the source is
    left in place. The flag is true: the caller must journal the move,
    then unlink the source. Unlinking first can delete the only copy.

    If the process dies after that rename and before the journal line, the
    bytes are already at ``final`` and the source is still there. Resume
    keeps that path when the bytes still match, instead of copying to a
    collision name. A crash after the journal line and before the source
    unlink is the other window: the done line is already there, so Resume
    would otherwise skip the row and leave the source in place.
    ``execute_plan`` removes that source when the destination still
    matches. A crash before the rename leaves the part file and the
    source. Cleanup deletes that part; it does not rename it onto
    ``final``.
    """
    try:
        os.replace(source, final)  # same volume: truly atomic
    except OSError:
        return atomic_copy(source, final), True
    return _sha256(final), False


def cleanup_stale_parts(dest_dir: Path | str) -> int:
    """Delete this app's ``.mediaorganizer.part`` files left by a crash.

    Only names ending in ``.mediaorganizer.part`` are touched. A file the
    user named ``notes.part`` or ``clip.mp4.part`` is left alone.

    The part is removed whether or not the final name (the part name with
    that suffix removed) already exists. It is never renamed onto that
    name.

    Promoting an orphan part is unsafe with the current copy and move
    order. ``atomic_copy`` writes the part, flushes and fsyncs it, then
    ``os.replace``s it to the final name. Only after that does
    ``execute_plan`` journal the file. A cross-volume move unlinks the
    source only after that journal line. A same-volume move uses
    ``os.replace`` on the source and never creates a part file. So when a
    part exists and the final name is missing, the source is still
    present. A crash before fsync can leave the part truncated. Publishing
    those bytes under the real filename makes resume fail the byte check,
    write the good file to a collision name, and on a move unlink the
    source.

    The part is not the only surviving copy after fsync plus a lost
    rename. That window is not how this app orders the writes.

    Returns how many part files were removed.
    """
    root = Path(dest_dir)
    n = 0
    if not root.is_dir():
        return 0
    for p in root.rglob(f"*{PART_SUFFIX}"):
        if not p.is_file() or not p.name.endswith(PART_SUFFIX):
            continue
        final_name = p.name[:-len(PART_SUFFIX)]
        if not final_name or final_name in (".", ".."):
            continue
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
