"""Display helpers: how paths, sizes and sort orders are shown. Pure logic."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def format_bytes(n: float) -> str:
    """Human size with 1 decimal: '812.5 KB', '3.9 MB', '48.2 GB'."""
    if n < 0:
        n = 0
    if n < 1024:
        return f"{n:.0f} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
    return f"{n:.1f} TB"


#: preview-table column id -> duck-typed PlannedFile sort key.
#: Returns None for "missing" values (they always sort last, both directions).
def plan_sort_key(column: str, item):
    """Sort key for one plan row. None => sorts last."""
    if column == "file":
        return item.source.name.lower()
    if column == "date":
        return item.capture.date  # datetime or None
    if column == "source":
        return item.capture.source.value if item.capture else ""
    if column == "dest":
        return str(item.destination).lower() if item.destination else None
    if column == "size":
        return item.size
    raise ValueError(f"unknown column: {column}")


def sorted_plan_items(plan: list, column: str, descending: bool = False) -> list:
    """Return the plan rows sorted for display; the plan itself is untouched.

    Items with a missing sort key (no date / no destination) stay at the
    bottom regardless of direction.
    """
    present = [p for p in plan if plan_sort_key(column, p) is not None]
    missing = [p for p in plan if plan_sort_key(column, p) is None]
    present.sort(key=lambda p: plan_sort_key(column, p), reverse=descending)
    return present + missing


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


def relative_destination_fast(destination: Path | str | None,
                              dest_root_norm: str | None) -> str:
    """String-only relative path for table rendering at scale.

    `dest_root_norm` must be os.path.normcase(os.path.normpath(root)) —
    computed ONCE per table fill. No resolve() calls: Path.resolve() costs
    ~ms per file on Windows, which froze the UI on multi-thousand-row plans.
    """
    if destination is None:
        return "—"
    if not dest_root_norm:
        return str(destination)
    import os
    raw = os.path.normpath(str(destination))
    d = os.path.normcase(raw)  # compare lowercased…
    prefix = dest_root_norm + os.sep
    if d.startswith(prefix):
        return raw[len(prefix):].replace(os.sep, "/")  # …but display original case
    return str(destination)


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
