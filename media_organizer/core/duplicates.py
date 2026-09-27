"""SHA-256 duplicate detection with a fast size + partial-hash pre-filter.

Hashing stages run on a thread pool: SHA-256 is implemented in C and
releases the GIL, so hashing scales near-linearly across cores.
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable, Optional

_PARTIAL_BYTES = 64 * 1024  # 64 KiB head+tail sample for the pre-filter


def _pool_workers() -> int:
    return min(32, (os.cpu_count() or 4) * 4)


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

    # Stage 2: partial-hash pre-filter within same-size groups (parallel)
    if cancel and cancel():
        return set()
    with ThreadPoolExecutor(max_workers=_pool_workers()) as pool:
        partials = list(pool.map(_safe_partial, candidates))
    if progress:
        progress(len(files) + len(candidates), candidates[-1].name)
    by_partial: dict[tuple[int, str], list[Path]] = {}
    for p, res in zip(candidates, partials):
        if res is None:
            continue
        by_partial.setdefault(res, []).append(p)

    # Stage 3: full hash only for partial collisions (parallel)
    hash_targets = [p for group in by_partial.values() if len(group) > 1
                    for p in group]
    if cancel and cancel():
        return set()
    with ThreadPoolExecutor(max_workers=_pool_workers()) as pool:
        fulls = list(pool.map(_safe_full, hash_targets))

    duplicates: set[Path] = set()
    seen: dict[str, Path] = {}
    for p, fh in zip(hash_targets, fulls):
        if fh is None:
            continue
        if fh in seen:
            duplicates.add(p)
        else:
            seen[fh] = p
    return duplicates


def _safe_partial(path: Path) -> Optional[tuple[int, str]]:
    try:
        size = path.stat().st_size
        return (size, _partial_hash(path, size))
    except OSError:
        return None


def _safe_full(path: Path) -> Optional[str]:
    try:
        return full_hash(path)
    except OSError:
        return None
