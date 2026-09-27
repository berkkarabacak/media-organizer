"""Plan building: scan, classify, and compute destination paths.

Destination folders come from the strategies in strategies.py; GPS-based
strategies resolve coordinates through geodata.py. Pure logic, no Qt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

from .geodata import location_label
from .metadata import CaptureDate, extract_capture_date, extract_gps
from .strategies import (DEFAULT_STRATEGY_KEY, STRATEGIES, UNDATED_FOLDER,
                         UNKNOWN_LOCATION_FOLDER, get_strategy, quarter_of)

IMAGE_EXTENSIONS = frozenset(
    {"jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp", "heic", "heif"}
)
VIDEO_EXTENSIONS = frozenset(
    {"mp4", "mov", "m4v", "avi", "mkv", "wmv", "flv", "3gp", "webm"}
)
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

__all__ = [
    "IMAGE_EXTENSIONS", "VIDEO_EXTENSIONS", "MEDIA_EXTENSIONS",
    "STRATEGIES", "UNDATED_FOLDER", "UNKNOWN_LOCATION_FOLDER",
    "DEFAULT_STRATEGY_KEY", "get_strategy", "quarter_of",
    "PlannedFile", "OrganizeOptions", "scan_media_files", "count_media_files",
    "build_plan",
]


@dataclass
class PlannedFile:
    source: Path
    destination: Optional[Path]  # None when skipped as duplicate
    size: int
    capture: CaptureDate
    kind: str  # "image" | "video"
    is_duplicate: bool = False
    location: Optional[str] = None  # resolved place label, e.g. "Istanbul, Turkey"
    error: str = ""


@dataclass
class OrganizeOptions:
    source_dir: Path
    dest_dir: Path
    recursive: bool = True
    strategy: str = DEFAULT_STRATEGY_KEY
    copy_mode: bool = True  # copy by default; move is explicit opt-in
    skip_duplicates: bool = True
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


def count_media_files(options: OrganizeOptions) -> int:
    """Fast pre-count of candidate media files (extension match only).

    Used to make scan progress determinate: no per-file stat/metadata reads,
    just one directory walk. Cheap even on large trees.
    """
    root = Path(options.source_dir)
    exts = options.effective_extensions()
    count = 0
    if options.recursive:
        walker = os.walk(root)
    else:
        walker = [(str(root), [], os.listdir(root) if root.is_dir() else [])]
    for _dirpath, _dirnames, filenames in walker:
        for name in filenames:
            if Path(name).suffix.lower().lstrip(".") in exts:
                count += 1
    return count


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


def _location_for(src: Path, cache: dict[tuple[float, float], Optional[str]]
                  ) -> Optional[str]:
    """Resolve a file's GPS coordinates to a place label (cached)."""
    coords = extract_gps(src)
    if coords is None:
        return None
    if coords not in cache:
        cache[coords] = location_label(*coords)
    return cache[coords]


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
    strategy = get_strategy(options.strategy)
    geo_cache: dict[tuple[float, float], Optional[str]] = {}

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
            plan.append(PlannedFile(src, None, size, capture, kind,
                                    is_duplicate=True))
            continue

        location = _location_for(src, geo_cache) if strategy.uses_location else None
        rel = strategy.relative_path(capture, location)
        dest_dir = Path(options.dest_dir).joinpath(*rel.split("/"))

        dest = _unique_destination(dest_dir, src.name, taken)
        plan.append(PlannedFile(src, dest, size, capture, kind,
                                location=location))

    return plan
