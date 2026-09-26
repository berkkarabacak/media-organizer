"""Display helpers: how paths are shown in the UI. Pure logic, no Qt."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def relative_destination(destination: Path | str | None,
                         dest_root: Path | str | None) -> str:
    """Destination path shown in the preview table.

    Returns the path *relative to the chosen destination root*
    (e.g. "2024/Q3/07 July/IMG_1234.jpg"). Falls back to the plain absolute
    path when it is not under the root; returns "—" for missing destinations.
    """
    if destination is None:
        return "—"
    dest = Path(destination)
    if dest_root:
        try:
            return dest.resolve().relative_to(Path(dest_root).resolve()).as_posix()
        except (ValueError, OSError):
            pass
        try:  # non-resolving fallback (paths that don't exist yet)
            return Path(os_path_norm(dest)).relative_to(
                os_path_norm(Path(dest_root))).as_posix()
        except ValueError:
            pass
    return str(dest)


def os_path_norm(path: Path) -> Path:
    """Normalise a path string without touching the filesystem."""
    import os
    return Path(os.path.normpath(str(path)))


def elide_middle(text: str, max_chars: int = 60) -> str:
    """Elide the middle of a long path, always keeping head and tail.

    'C:/very/long/.../folder/IMG_1234.jpg' instead of a bare 'C:\\...'.
    """
    if max_chars < 10:
        max_chars = 10
    if len(text) <= max_chars:
        return text
    keep = max_chars - 3  # for "..."
    head = keep // 2
    tail = keep - head
    return text[:head] + "..." + text[len(text) - tail:]
