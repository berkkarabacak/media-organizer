"""Capture-date extraction with a fallback chain.

Order of attempts:
  1. EXIF DateTimeOriginal / DateTimeDigitized / DateTime (JPEG, TIFF, HEIC-adjacent)
  2. PNG text chunks (tEXt / zTXt containing a creation time)
  3. Video container creation time (MP4/MOV/M4V/3GP mvhd/mdhd atoms, pure-Python)
  4. Filename date patterns
  5. Filesystem mtime (low confidence)
"""

from __future__ import annotations

import os
import re
import struct
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

# Seconds between 1904-01-01 (QuickTime/MP4 epoch) and 1970-01-01 (Unix epoch).
QT_EPOCH_OFFSET = 2082844800


class DateSource(str, Enum):
    EXIF = "exif"
    PNG_TEXT = "png_text"
    VIDEO = "video"
    FILENAME = "filename"
    MTIME = "mtime"
    NONE = "none"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class CaptureDate:
    """Result of capture-date detection for one file."""

    date: Optional[datetime]
    source: DateSource = DateSource.NONE
    confidence: Confidence = Confidence.LOW
    detail: str = ""

    @property
    def found(self) -> bool:
        return self.date is not None


# EXIF tag ids (per EXIF spec)
_EXIF_TAGS = (0x9003, 0x9004, 0x0132)  # DateTimeOriginal, DateTimeDigitized, DateTime

_EXIF_DATETIME_RE = re.compile(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")

_GENERIC_DATETIME_RE = re.compile(
    r"(\d{4})[-:/ ](\d{1,2})[-:/ ](\d{1,2})[ T_]?(\d{1,2})?[:\.]?(\d{2})?[:\.]?(\d{2})?"
)


def _parse_exif_datetime(value) -> Optional[datetime]:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii", errors="ignore")
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    value = value.strip().strip("\x00").strip()
    m = _EXIF_DATETIME_RE.match(value)
    if not m:
        return None
    try:
        return datetime(*[int(g) for g in m.groups()])
    except ValueError:
        return None


def _exif_date(path: Path) -> Optional[datetime]:
    """Read EXIF capture date via Pillow (JPEG / TIFF / HEIC-adjacent)."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            exif = None
            try:
                exif = img.getexif()
            except Exception:
                exif = None
            if not exif:
                return None
            # Direct tags first
            for tag in _EXIF_TAGS:
                raw = exif.get(tag)
                dt = _parse_exif_datetime(raw)
                if dt:
                    return dt
            # Exif IFD pointer (0x8769) holds DateTimeOriginal for most JPEGs
            try:
                ifd = exif.get_ifd(0x8769)
                for tag in _EXIF_TAGS:
                    raw = ifd.get(tag)
                    dt = _parse_exif_datetime(raw)
                    if dt:
                        return dt
            except Exception:
                pass
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# PNG text chunks
# ---------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_PNG_DATE_KEYS = (
    "creation time",
    "date:create",
    "date:modify",
    "exif:datetimeoriginal",
    "exif:datetime",
    "date",
)


def _png_text_date(path: Path) -> Optional[datetime]:
    try:
        with open(path, "rb") as fh:
            sig = fh.read(8)
            if sig != _PNG_SIG:
                return None
            while True:
                header = fh.read(8)
                if len(header) < 8:
                    return None
                (length,) = struct.unpack(">I", header[:4])
                ctype = header[4:8]
                data = fh.read(length)
                fh.read(4)  # CRC
                if len(data) < length:
                    return None
                if ctype == b"IEND":
                    return None
                if ctype == b"tEXt":
                    key, _, value = data.partition(b"\x00")
                    dt = _parse_generic_datetime(
                        key.decode("latin-1", "ignore"), value.decode("latin-1", "ignore")
                    )
                    if dt:
                        return dt
                elif ctype == b"zTXt":
                    key, _, rest = data.partition(b"\x00")
                    if rest and rest[0] == 0:  # zlib compression
                        try:
                            value = zlib.decompress(rest[1:]).decode("latin-1", "ignore")
                        except Exception:
                            continue
                        dt = _parse_generic_datetime(key.decode("latin-1", "ignore"), value)
                        if dt:
                            return dt
    except Exception:
        return None
    return None


def _parse_generic_datetime(key: str, value: str) -> Optional[datetime]:
    if key.strip().lower() not in _PNG_DATE_KEYS:
        return None
    value = value.strip()
    # RFC-1123 style (PNG "Creation Time" convention)
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%d %b %Y %H:%M:%S", "%Y:%m:%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    m = _GENERIC_DATETIME_RE.search(value)
    if m:
        y, mo, d, h, mi, s = m.groups()
        try:
            return datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# MP4/MOV box parsing (pure Python)
# ---------------------------------------------------------------------------

def _iter_boxes(fh, end: int):
    """Yield (box_type, payload_start, box_end) tuples for top-level children."""
    while True:
        pos = fh.tell()
        if pos + 8 > end:
            return
        header = fh.read(8)
        if len(header) < 8:
            return
        size, btype = struct.unpack(">I4s", header)
        header_size = 8
        if size == 1:
            largesize = fh.read(8)
            if len(largesize) < 8:
                return
            (size,) = struct.unpack(">Q", largesize)
            header_size = 16
        elif size == 0:
            size = end - pos
        if size < header_size or pos + size > end + 8:
            return
        yield btype, pos + header_size, pos + size
        fh.seek(pos + size)


def _qt_time_to_datetime(raw: int) -> Optional[datetime]:
    if raw <= 0:
        return None
    ts = raw - QT_EPOCH_OFFSET
    if ts < 0:
        return None
    try:
        dt = datetime.fromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return None
    # sanity window: nothing before 1980, nothing in the far future
    if dt.year < 1980 or dt.year > 2100:
        return None
    return dt


def _parse_mvhd(payload: bytes) -> Optional[datetime]:
    """Parse a Movie Header box payload (version 0 and 1)."""
    if len(payload) < 4:
        return None
    version = payload[0]
    if version == 1:
        if len(payload) < 20:
            return None
        (creation,) = struct.unpack(">Q", payload[4:12])
    else:  # version 0
        if len(payload) < 12:
            return None
        (creation,) = struct.unpack(">I", payload[4:8])
    return _qt_time_to_datetime(creation)


def _parse_mdhd(payload: bytes) -> Optional[datetime]:
    if len(payload) < 4:
        return None
    version = payload[0]
    if version == 1:
        if len(payload) < 20:
            return None
        (creation,) = struct.unpack(">Q", payload[4:12])
    else:
        if len(payload) < 12:
            return None
        (creation,) = struct.unpack(">I", payload[4:8])
    return _qt_time_to_datetime(creation)


def _parse_quicktime_meta_date(data: bytes) -> Optional[datetime]:
    """MOV 'meta' boxes sometimes carry an XML plist with a com.apple.quicktime.creationdate."""
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", text)
    if m:
        try:
            return datetime(*[int(g) for g in m.groups()])
        except ValueError:
            return None
    return None


def _video_creation_date(path: Path) -> Optional[datetime]:
    """Extract creation time from an MP4/MOV/M4V/3GP container."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            file_end = fh.tell()
            fh.seek(0)
            container_types = {b"moov", b"trak", b"mdia", b"udta"}
            stack = [(fh, 0, file_end)]
            dates: list[datetime] = []
            while stack:
                f, start, end = stack.pop()
                f.seek(start)
                for btype, payload_start, box_end in _iter_boxes(f, end):
                    if btype == b"mvhd" or btype == b"mdhd":
                        f.seek(payload_start)
                        payload = f.read(min(box_end - payload_start, 64))
                        parser = _parse_mvhd if btype == b"mvhd" else _parse_mdhd
                        dt = parser(payload)
                        if dt:
                            dates.append(dt)
                    elif btype in container_types:
                        stack.append((f, payload_start, box_end))
                    elif btype == b"meta":
                        f.seek(payload_start)
                        payload = f.read(min(box_end - payload_start, 65536))
                        dt = _parse_quicktime_meta_date(payload)
                        if dt:
                            dates.append(dt)
            if dates:
                return min(dates)  # earliest track/movie creation time
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Filename patterns
# ---------------------------------------------------------------------------

_FILENAME_PATTERNS: list[tuple[re.Pattern, str]] = [
    # IMG_20240115_123000 / VID_20240115_123000 / PXL_20240115_123000
    (re.compile(r"(?:^|[_\-\s])(?:IMG|VID|PXL|MVIMG|BURST|DSC|DSCF|SAM_?)?[_\-]?"
                r"(\d{4})(\d{2})(\d{2})[_\-](\d{2})(\d{2})(\d{2})", re.IGNORECASE),
     "Ymd_HMS"),
    # Screenshot_2024-01-15-12-30-00 / Screenshot_20240115-123000
    (re.compile(r"Screenshot[_\-](\d{4})[-]?(\d{2})[-]?(\d{2})[_\-]?(\d{2})?[-]?(\d{2})?[-]?(\d{2})?",
                re.IGNORECASE),
     "screenshot"),
    # WhatsApp: IMG-20240115-WA0001
    (re.compile(r"(?:IMG|VID|AUD|PTT|DOC)-(\d{4})(\d{2})(\d{2})-WA\d+", re.IGNORECASE),
     "whatsapp"),
    # 2024-01-15 12.30.00 (Samsung/MyPhone style)
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T_](\d{2})[\.\-:](\d{2})[\.\-:](\d{2})"),
     "Y-m-d H.M.S"),
    # 2024-01-15 alone
    (re.compile(r"(?:^|[^\d])(\d{4})-(\d{2})-(\d{2})(?:[^\d]|$)"), "Y-m-d"),
    # bare YYYYMMDD anywhere (last resort within filename)
    (re.compile(r"(?<!\d)((?:19|20)\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)"),
     "Ymd"),
]


def _filename_date(name: str) -> Optional[datetime]:
    stem = Path(name).stem
    for pattern, _label in _FILENAME_PATTERNS:
        m = pattern.search(stem)
        if not m:
            continue
        groups = [int(g) if g is not None else 0 for g in m.groups()]
        groups += [0] * (6 - len(groups))
        y, mo, d, h, mi, s = groups[:6]
        try:
            return datetime(y, mo, d, h, mi, s)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# EXIF GPS
# ---------------------------------------------------------------------------

_GPS_IFD = 0x8825  # GPSInfo IFD pointer
_TAG_GPS_LAT_REF = 1
_TAG_GPS_LAT = 2
_TAG_GPS_LON_REF = 3
_TAG_GPS_LON = 4


def _dms_to_decimal(dms, ref) -> Optional[float]:
    """Convert EXIF (degrees, minutes, seconds) + hemisphere ref to decimal."""
    try:
        deg, minutes, seconds = (float(v) for v in dms[:3])
    except (TypeError, ValueError, IndexError):
        return None
    dec = deg + minutes / 60.0 + seconds / 3600.0
    if isinstance(ref, bytes):
        ref = ref.decode("ascii", errors="ignore")
    if str(ref).strip().upper() in ("S", "W"):
        dec = -dec
    if not -180.0 <= dec <= 180.0:
        return None
    return dec


def extract_gps(path: os.PathLike | str) -> Optional[tuple[float, float]]:
    """Read EXIF GPS coordinates. Returns (lat, lon) or None. Never raises."""
    path = Path(path)
    if path.suffix.lower().lstrip(".") not in (
            "jpg", "jpeg", "tif", "tiff", "heic", "heif", "webp"):
        return None
    try:
        from PIL import Image

        with Image.open(path) as img:
            try:
                exif = img.getexif()
            except Exception:
                return None
            if not exif:
                return None
            gps = exif.get_ifd(_GPS_IFD)
            if not gps:
                return None
            lat = _dms_to_decimal(gps.get(_TAG_GPS_LAT), gps.get(_TAG_GPS_LAT_REF))
            lon = _dms_to_decimal(gps.get(_TAG_GPS_LON), gps.get(_TAG_GPS_LON_REF))
            if lat is None or lon is None:
                return None
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                return None
            if lat == 0.0 and lon == 0.0:
                return None  # null-island placeholder, treat as unknown
            return (lat, lon)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_capture_date(path: os.PathLike | str, *, include_mtime: bool = True) -> CaptureDate:
    """Detect the real capture date of a media file.

    Never raises: unreadable or corrupt files fall through the chain and end
    up with mtime (low confidence) or NONE.
    """
    path = Path(path)
    ext = path.suffix.lower().lstrip(".")

    # 1. EXIF (images)
    if ext in ("jpg", "jpeg", "tif", "tiff", "heic", "heif", "webp", "png"):
        dt = _exif_date(path)
        if dt:
            return CaptureDate(dt, DateSource.EXIF, Confidence.HIGH, "EXIF capture date")

    # 2. PNG text chunks
    if ext == "png":
        dt = _png_text_date(path)
        if dt:
            return CaptureDate(dt, DateSource.PNG_TEXT, Confidence.HIGH, "PNG text chunk")

    # 3. Video container creation time
    if ext in ("mp4", "mov", "m4v", "3gp"):
        dt = _video_creation_date(path)
        if dt:
            return CaptureDate(dt, DateSource.VIDEO, Confidence.HIGH, "video container metadata")

    # 4. Filename patterns
    dt = _filename_date(path.name)
    if dt:
        return CaptureDate(dt, DateSource.FILENAME, Confidence.MEDIUM, "date in filename")

    # 5. Filesystem mtime
    if include_mtime:
        try:
            ts = path.stat().st_mtime
            return CaptureDate(
                datetime.fromtimestamp(ts),
                DateSource.MTIME,
                Confidence.LOW,
                "filesystem modified time (unreliable)",
            )
        except OSError:
            pass

    return CaptureDate(None, DateSource.NONE, Confidence.LOW, "no date found")
