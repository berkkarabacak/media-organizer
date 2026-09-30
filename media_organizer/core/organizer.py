"""Plan building: scan, classify, and compute destination paths.

Destination folders come from the strategies in strategies.py; GPS-based
strategies resolve coordinates through geodata.py. Pure logic, no Qt.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

from .geodata import location_label
from .journal import files_identical, find_unfinished_journal, path_identity
from .metadata import (CaptureDate, DateSource, extract_capture_date,
                       extract_gps)
from .plan import saved_run_destinations
from .strategies import (DEFAULT_STRATEGY_KEY, STRATEGIES, UNCERTAIN_FOLDER,
                         UNDATED_FOLDER, UNKNOWN_LOCATION_FOLDER,
                         get_strategy, quarter_of)

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
    "build_plan", "destination_blocks_scan",
]


def destination_blocks_scan(source: os.PathLike | str,
                           dest: os.PathLike | str) -> Optional[str]:
    """Why this destination cannot be scanned, or None if it is usable.

    The scan skips every file that already lives inside the destination.
    That is right when the destination is a folder *inside* the source
    (the usual "Organized" folder). It finds nothing — with no other
    signal — when the destination *is* the source, or is a parent of it,
    because the whole source is then inside the destination.

    Returns a sentence the UI can show before anything is moved.
    """
    try:
        src = Path(source).resolve()
        dst = Path(dest).resolve()
    except OSError:
        return None
    src_n = os.path.normcase(os.path.normpath(str(src)))
    dst_n = os.path.normcase(os.path.normpath(str(dst)))
    if src_n == dst_n:
        return (
            "The destination is the same folder as the source. "
            "Files that already live in the destination are left alone, "
            "so this scan would find nothing and nothing would be organized. "
            "Choose a different folder before you continue."
        )
    if src_n.startswith(dst_n + os.sep):
        return (
            "The destination is a parent of the source folder. "
            "Every file is already inside the destination, so this scan "
            "would find nothing and nothing would be organized. "
            "Choose a folder that is not above your photos."
        )
    return None


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
    dry_run: bool = False   # simulate the full run, write nothing
    # "aside": mtime-only guesses go to _uncertain/; "use": file date is used
    uncertain: str = "aside"
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


# Directory reparse points (junctions, mount points, symlink dirs). Windows
# sets this even when os.path.islink is false, which is why os.walk follows
# junctions with followlinks left at the default.
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _normalized(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def _is_same_or_inside(path_norm: str, root_norm: str) -> bool:
    return path_norm == root_norm or path_norm.startswith(root_norm + os.sep)


def _stat_is_non_descendable_dir(st: object, path: str) -> bool:
    """True for a directory symlink, or a Windows junction / reparse directory.

    `path` is only used for the isjunction fallback when the stat result has
    no file-attribute field. A Windows stat that includes the field is
    authoritative, so ordinary directories do not pay for a second check.
    """
    if stat.S_ISLNK(getattr(st, "st_mode", 0)):
        return True
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is not None and attrs & _REPARSE_POINT:
        return True
    if attrs is not None or os.name != "nt":
        return False
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is None:
        return False
    try:
        return bool(isjunction(path))
    except OSError:
        return False


def _is_non_descendable_dir(path: str) -> bool:
    """Directory symlink on any platform, or a Windows directory reparse point.

    os.walk(followlinks=False) already skips directory symlinks, but a
    junction is not a symlink: islink() is false and the walk follows it.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return _stat_is_non_descendable_dir(st, path)


def _under_resolved_dest(dirpath: str, dest_norm: str,
                         link_dirs: dict[str, bool]) -> bool:
    """Whether this walk directory is the destination or already inside it.

    The walk path is compared first, with no syscall. A junction or symlink
    is resolved only when that string is a different path from the real
    folder — never once per file.
    """
    if _is_same_or_inside(_normalized(dirpath), dest_norm):
        return True
    key = _normalized(dirpath)
    if key not in link_dirs:
        link_dirs[key] = _is_non_descendable_dir(dirpath)
    if not link_dirs[key]:
        return False
    try:
        resolved = _normalized(Path(dirpath).resolve())
    except OSError:
        return False
    return _is_same_or_inside(resolved, dest_norm)


def _drop_non_descendable(dirpath: str, dirnames: list[str],
                          link_dirs: dict[str, bool]) -> None:
    """Remove symlink and junction directories from dirnames, in place.

    os.walk reads this same list after the yield to decide where to go
    next, so replacing the list object would not stop the descent.
    """
    kept: list[str] = []
    for name in dirnames:
        child = os.path.join(dirpath, name)
        key = _normalized(child)
        if key not in link_dirs:
            link_dirs[key] = _is_non_descendable_dir(child)
        if link_dirs[key]:
            continue
        kept.append(name)
    if len(kept) != len(dirnames):
        dirnames[:] = kept


def _iter_candidate_dirs(options: OrganizeOptions
                         ) -> Iterator[tuple[str, list[str]]]:
    """Yield (dirpath, filenames) for both the scan and the progress count.

    The resolved destination tree is skipped. Directory symlinks and Windows
    junction / reparse-point directories are not descended into, so a
    destination created with mklink /J, or a junction that loops back into
    the source, is not scanned.
    """
    root = Path(options.source_dir).resolve()
    dest_norm = _normalized(Path(options.dest_dir).resolve())
    link_dirs: dict[str, bool] = {}

    if options.recursive:
        walker = os.walk(root, followlinks=False)
    else:
        try:
            names = os.listdir(root) if root.is_dir() else []
        except OSError:
            names = []
        walker = [(os.fspath(root), [], names)]

    for dirpath, dirnames, filenames in walker:
        if _under_resolved_dest(dirpath, dest_norm, link_dirs):
            # In-place: os.walk must not descend into the destination.
            dirnames[:] = []
            continue
        if options.recursive:
            _drop_non_descendable(dirpath, dirnames, link_dirs)
        yield dirpath, filenames


def count_media_files(options: OrganizeOptions) -> int:
    """Fast pre-count of candidate media files (extension match only).

    Uses the same directories as the scan, including the destination prune
    and the refusal to follow junctions or directory symlinks, so a progress
    total matches the files the scan will yield. No per-file stat or
    metadata read.
    """
    exts = options.effective_extensions()
    count = 0
    for _dirpath, filenames in _iter_candidate_dirs(options):
        for name in filenames:
            if Path(name).suffix.lower().lstrip(".") in exts:
                count += 1
    return count


def scan_media_files(options: OrganizeOptions) -> Iterator[Path]:
    """Yield candidate media files under source_dir.

    The destination tree is pruned at the directory level. resolve() runs
    on the source, the destination, and a walk directory only when that
    directory is itself a symlink or junction — not on every file.
    """
    exts = options.effective_extensions()
    for dirpath, filenames in _iter_candidate_dirs(options):
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix.lower().lstrip(".") not in exts:
                continue
            try:
                if not p.is_file():
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


def _planned_destination(dest_dir: Path, src: Path, taken: set[str], *,
                         reuse_identical: bool, owned: set[str]) -> Path:
    """Destination for ``src``, reusing a published match on resume.

    The crash window is a final name already on disk with no journal line
    for this source. When those bytes still match, keep ``dest_dir/src.name``
    instead of ``name_1``. Mark it taken so a later row cannot share it.

    A path a saved run already logged is not reused. That copy belongs to
    the earlier run, so a newer organize still gets a collision suffix and
    undo of the newer run can leave the earlier file in place. A file that
    merely exists and does not match is never overwritten.
    """
    natural = dest_dir / src.name
    key = str(natural).lower()
    if (reuse_identical
            and key not in taken
            and path_identity(natural) not in owned
            and files_identical(src, natural)):
        taken.add(key)
        return natural
    return _unique_destination(dest_dir, src.name, taken)


def _location_for(src: Path, cache: dict[tuple[float, float], Optional[str]]
                  ) -> Optional[str]:
    """Resolve a file's GPS coordinates to a place label (cached)."""
    return _location_label_for(extract_gps(src), cache)


def build_plan(
    options: OrganizeOptions,
    duplicates: Optional[set[Path]] = None,
    progress: Optional[Callable[[int, str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
    files: Optional[list[Path]] = None,
    analysis: Optional[dict] = None,
) -> list[PlannedFile]:
    """Build the full organise plan (dry run). Does not write or rename.

    On resume, a natural destination that is still a byte-for-byte match of
    its source is kept. That is the crash after the final name was published
    and before the journal line. Any other occupant gets a collision suffix.

    `files` and `analysis` ({path: (CaptureDate, gps)}) let the caller reuse
    an already-listed/parallel-analyzed batch (the GUI scan does this).
    """
    duplicates = duplicates or set()
    plan: list[PlannedFile] = []
    taken: set[str] = set()
    # Only an interrupted run should adopt a file it already published.
    # A finished journal means the next organize is a new run.
    resuming = find_unfinished_journal(options.dest_dir) is not None
    owned = (saved_run_destinations(options.dest_dir)
             if resuming else set())
    strategy = get_strategy(options.strategy)
    geo_cache: dict[tuple[float, float], Optional[str]] = {}
    analysis = analysis or {}

    for i, src in enumerate(files if files is not None
                            else scan_media_files(options)):
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
        if src in analysis:
            capture, coords = analysis[src]
        else:
            capture = extract_capture_date(src)
            coords = extract_gps(src) if strategy.uses_location else None

        if src in duplicates and options.skip_duplicates:
            plan.append(PlannedFile(src, None, size, capture, kind,
                                    is_duplicate=True))
            continue

        # mtime-only guesses ("file date (guess)") are unreliable after
        # copies — set them aside instead of misfiling them by date/place
        location = None
        if (options.uncertain == "aside" and capture.found
                and capture.source is DateSource.MTIME):
            rel = UNCERTAIN_FOLDER
        elif strategy.uses_location:
            location = _location_label_for(coords, geo_cache)
            rel = strategy.relative_path(capture, location)
        else:
            rel = strategy.relative_path(capture, location)
        dest_dir = Path(options.dest_dir).joinpath(*rel.split("/"))

        dest = _planned_destination(
            dest_dir, src, taken, reuse_identical=resuming, owned=owned)
        plan.append(PlannedFile(src, dest, size, capture, kind,
                                location=location))

    return plan


def _location_label_for(coords: Optional[tuple[float, float]],
                        cache: dict) -> Optional[str]:
    if coords is None:
        return None
    if coords not in cache:
        cache[coords] = location_label(*coords)
    return cache[coords]
