"""Crash-resilience journal: append-only JSONL per run in the destination.

Each completed file operation is journaled immediately, so a power cut or
crash mid-run leaves a resumable record. Copies are written to a temp name
and atomically renamed, so a half-written file never appears at its final
name. Pure logic, no Qt.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Iterator, Optional

JOURNAL_DIRNAME = ".mediaorganizer-journal"
JOURNAL_FILENAME = "operations.jsonl"
PART_SUFFIX = ".part"


def journal_path_for(dest_dir: Path | str) -> Path:
    return Path(dest_dir) / JOURNAL_DIRNAME / JOURNAL_FILENAME


class JournalWriter:
    """Append-only JSONL journal for one run."""

    def __init__(self, dest_dir: Path | str):
        self.path = journal_path_for(dest_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

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


def find_unfinished_journal(dest_dir: Path | str) -> Optional[Path]:
    """Return the journal path if a previous run was interrupted."""
    path = journal_path_for(dest_dir)
    if not path.exists():
        return None
    completed = False
    for entry in _journal_entries(path):
        if entry.get("run") == "complete":
            completed = True
    return None if completed else path


def completed_sources(journal_path: Path | str) -> set[str]:
    """Sources fully written in the interrupted run (safe to skip on resume)."""
    return {e["source"] for e in _journal_entries(Path(journal_path))
            if e.get("status") == "done" and e.get("source")}


def completed_destinations(journal_path: Path | str) -> set[str]:
    return {e["destination"] for e in _journal_entries(Path(journal_path))
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


def atomic_copy(source: Path, final: Path) -> None:
    """Copy via temp name + atomic rename.

    A crash mid-copy leaves only a `.part` file, never a truncated file at
    the final name. Stale `.part` files from previous crashes are replaced.
    """
    part = final.with_name(final.name + PART_SUFFIX)
    try:
        part.unlink()
    except OSError:
        pass
    shutil.copy2(source, part)
    os.replace(part, final)


def atomic_move(source: Path, final: Path) -> None:
    """Move with crash safety: atomic same-volume when possible, else
    copy-to-.part + rename + delete source."""
    try:
        os.replace(source, final)  # same volume: truly atomic
    except OSError:
        atomic_copy(source, final)
        source.unlink()


def cleanup_stale_parts(dest_dir: Path | str) -> int:
    """Remove leftover .part files from crashed runs. Returns count."""
    root = Path(dest_dir)
    n = 0
    if not root.is_dir():
        return 0
    for p in root.rglob(f"*{PART_SUFFIX}"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
