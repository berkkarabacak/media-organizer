"""Plan building: scan, classify, and compute destination paths.

Patterns support the tokens YYYY, MM, MonthName, Q (quarter number).
Quarter = (month - 1) // 3 + 1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Optional

from .metadata import CaptureDate, extract_capture_date

IMAGE_EXTENSIONS = frozenset(
    {"jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp", "heic", "heif"}
)
VIDEO_EXTENSIONS = frozenset(
    {"mp4", "mov", "m4v", "avi", "mkv", "wmv", "flv", "3gp", "webm"}
)
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

# Built-in destination patterns (relative subfolder templates)
PATTERNS = {
    "year_month": "YYYY/MM (MonthName)",
    "year_quarter": "YYYY/Q#",
    "year_month_quarter": "YYYY/MM/Q#",
}
DEFAULT_PATTERN = PATTERNS["year_month"]

UNDATED_FOLDER = "Undated"


def quarter_of(month: int) -> int:
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {month}")
    return (month - 1) // 3 + 1


def render_pattern(pattern: str, dt: datetime) -> str:
    """Expand pattern tokens for a date. Always uses safe path segments."""
    q = quarter_of(dt.month)
    out = pattern
    out = out.replace("YYYY", f"{dt.year:04d}")
    out = out.replace("MonthName", MONTH_NAMES[dt.month - 1])
    out = out.replace("MM", f"{dt.month:02d}")
    out = out.replace("Q#", f"Q{q}")
    # Normalise separators for the host OS
    return out.replace("/", os.sep)


@dataclass
class PlannedFile:
    source: Path
    destination: Optional[Path]  # None when skipped as duplicate
    size: int
    capture: CaptureDate
    kind: str  # "image" | "video"
    is_duplicate: bool = False
    error: str = ""


@dataclass
class OrganizeOptions:
    source_dir: Path
    dest_dir: Path
    recursive: bool = True
    pattern: str = DEFAULT_PATTERN
    copy_mode: bool = True  # copy by default; move is explicit opt-in
    skip_duplicates: bool = False
    include_images: bool = True
    include_videos: bool = True
    extensions: Optional[frozenset] = None  # explicit override

    def effective_extensions(self) -> frozenset:
        if self.extensions is not None:
            return self.extensions
        exts: set[str] = set()
        if self.include_images:
            exts |= IMAGE_EXTENSIONS
        if self.include_videos:
            exts |= VIDEO_EXTENSIONS
        return frozenset(exts)


def scan_media_files(options: OrganizeOptions) -> Iterator[Path]:
    """Yield candidate media files under source_dir."""
    root = Path(options.source_dir)
    exts = options.effective_extensions()
    dest_root = Path(options.dest_dir).resolve()

    if options.recursive:
        walker = os.walk(root)
    else:
        walker = [(str(root), [], os.listdir(root) if root.is_dir() else [])]

    for dirpath, _dirnames, filenames in walker:
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix.lower().lstrip(".") not in exts:
                continue
            try:
                if not p.is_file():
                    continue
                # Never organise files that already live inside the destination tree
                if dest_root in p.resolve().parents or p.resolve() == dest_root:
                    continue
            except OSError:
                continue
            yield p


def _unique_destination(dest_dir: Path, filename: str, taken: set[str]) -> Path:
    """Compute a collision-free destination; never overwrites."""
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = dest_dir / filename
    n = 0
    while str(candidate).lower() in taken or candidate.exists():
        n += 1
        candidate = dest_dir / f"{stem}_{n}{suffix}"
    taken.add(str(candidate).lower())
    return candidate


def build_plan(
    options: OrganizeOptions,
    duplicates: Optional[set[Path]] = None,
    progress: Optional[Callable[[int, str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> list[PlannedFile]:
    """Build the full organise plan (dry run). Pure: touches nothing."""
    duplicates = duplicates or set()
    plan: list[PlannedFile] = []
    taken: set[str] = set()

    for i, src in enumerate(scan_media_files(options)):
        if cancel and cancel():
            break
        if progress:
            progress(i, src.name)
        try:
            size = src.stat().st_size
        except OSError as exc:
            plan.append(PlannedFile(src, None, 0, CaptureDate(None), "unknown",
                                    error=str(exc)))
            continue

        ext = src.suffix.lower().lstrip(".")
        kind = "image" if ext in IMAGE_EXTENSIONS else "video"
        capture = extract_capture_date(src)

        if src in duplicates and options.skip_duplicates:
            plan.append(PlannedFile(src, None, size, capture, kind, is_duplicate=True))
            continue

        if capture.found:
            sub = render_pattern(options.pattern, capture.date)
            dest_dir = Path(options.dest_dir) / sub
        else:
            dest_dir = Path(options.dest_dir) / UNDATED_FOLDER

        dest = _unique_destination(dest_dir, src.name, taken)
        plan.append(PlannedFile(src, dest, size, capture, kind))

    return plan
