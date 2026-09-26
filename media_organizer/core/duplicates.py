"""SHA-256 duplicate detection with a fast size + partial-hash pre-filter."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Iterable, Optional

_PARTIAL_BYTES = 64 * 1024  # 64 KiB head+tail sample for the pre-filter


def _partial_hash(path: Path, size: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        head = fh.read(_PARTIAL_BYTES)
        h.update(head)
        if size > _PARTIAL_BYTES * 2:
            fh.seek(size - _PARTIAL_BYTES)
            h.update(fh.read(_PARTIAL_BYTES))
    return h.hexdigest()


def full_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(
    files: Iterable[Path],
    progress: Optional[Callable[[int, str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> set[Path]:
    """Return the set of files that are duplicates of an earlier-seen file.

    The first occurrence of each unique payload is kept; later identical
    files are reported as duplicates. Unreadable files are ignored.
    """
    files = list(files)
    # Stage 1: group by size (free)
    by_size: dict[int, list[Path]] = {}
    for i, p in enumerate(files):
        if cancel and cancel():
            return set()
        if progress and i % 200 == 0:
            progress(i, p.name)
        try:
            size = p.stat().st_size
        except OSError:
            continue
        by_size.setdefault(size, []).append(p)

    candidates = [p for group in by_size.values() if len(group) > 1 for p in group]
    if not candidates:
        return set()

    # Stage 2: partial-hash pre-filter within same-size groups
    by_partial: dict[tuple[int, str], list[Path]] = {}
    for i, p in enumerate(candidates):
        if cancel and cancel():
            return set()
        if progress:
            progress(len(files) + i, p.name)
        try:
            size = p.stat().st_size
            ph = _partial_hash(p, size)
        except OSError:
            continue
        by_partial.setdefault((size, ph), []).append(p)

    # Stage 3: full hash only for partial collisions
    duplicates: set[Path] = set()
    for group in by_partial.values():
        if len(group) < 2:
            continue
        seen: dict[str, Path] = {}
        for p in group:
            if cancel and cancel():
                return duplicates
            try:
                fh = full_hash(p)
            except OSError:
                continue
            if fh in seen:
                duplicates.add(p)
            else:
                seen[fh] = p
    return duplicates
