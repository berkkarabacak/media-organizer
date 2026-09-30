"""Crash-resilience journal: one JSONL file per destination.

Each completed file operation is journaled immediately, so a power cut or
crash mid-run leaves a resumable record. A new organize replaces that file.
Resuming an interrupted run appends to the same file until it is marked
complete. Copies are written to a temp name and atomically renamed, so a
half-written file never appears at its final name. Pure logic, no Qt.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Iterator, Optional

JOURNAL_DIRNAME = ".mediaorganizer-journal"
JOURNAL_FILENAME = "operations.jsonl"
# Distinct from a user file that merely ends in ".part". Incomplete copies
# are named "<final name>.mediaorganizer.part" and only those are deleted.
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
    """

    def __init__(self, dest_dir: Path | str, *, resume: bool | None = None):
        self.path = journal_path_for(dest_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if resume is None:
            resume = find_unfinished_journal(dest_dir) is not None
        # "w" drops a finished journal so a later interrupt is not hidden by
        # an older complete marker. "a" keeps the open run's records.
        self._fh = open(self.path, "a" if resume else "w", encoding="utf-8")

    def record(self, action: str, source: str, destination: str) -> None:
        self._fh.write(json.dumps(
            {"action": action, "source": source,
             "destination": destination, "status": "done"}) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def complete(self) -> None:
        self._fh.write(json.dumps({"run": "complete"}) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self._fh.close()

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


def completed_sources(journal_path: Path | str) -> set[str]:
    """Sources fully written in the interrupted run (safe to skip on resume).

    Sources from a run that already wrote ``{"run": "complete"}`` are not
    included. Those files belong to a finished organize, not this resume.
    """
    entries, _finished = _open_run(Path(journal_path))
    return {e["source"] for e in entries
            if e.get("status") == "done" and e.get("source")}


def completed_destinations(journal_path: Path | str) -> set[str]:
    entries, _finished = _open_run(Path(journal_path))
    return {e["destination"] for e in entries
            if e.get("status") == "done" and e.get("destination")}


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


def atomic_copy(source: Path, final: Path) -> str:
    """Copy via temp name + atomic rename. Returns the sha256 of the bytes.

    A crash mid-copy leaves only a ``.mediaorganizer.part`` file, never a
    truncated file at the final name. A stale part for this same destination
    is replaced; other ``*.part`` files are not touched.
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
    shutil.copystat(source, part)
    os.replace(part, final)
    return digest.hexdigest()


def atomic_move(source: Path, final: Path) -> str:
    """Move with crash safety. Returns the sha256 of the bytes now at `final`.

    Same-volume renames are atomic. Across volumes the file is copied to a
    ``.mediaorganizer.part`` name, renamed, then the source is removed.
    """
    try:
        os.replace(source, final)  # same volume: truly atomic
    except OSError:
        digest = atomic_copy(source, final)
        source.unlink()
        return digest
    return _sha256(final)


def cleanup_stale_parts(dest_dir: Path | str) -> int:
    """Remove this app's incomplete copies left by a crashed run.

    Only names ending in ``.mediaorganizer.part`` are removed. A file the
    user named ``notes.part`` or ``clip.mp4.part`` is left alone.
    Returns how many files were removed.
    """
    root = Path(dest_dir)
    n = 0
    if not root.is_dir():
        return 0
    for p in root.rglob(f"*{PART_SUFFIX}"):
        if not p.is_file() or not p.name.endswith(PART_SUFFIX):
            continue
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
