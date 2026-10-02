"""Creation dates from video containers other than MP4/MOV.

MP4/MOV/M4V/3GP stay in metadata.py (QuickTime atoms). This module covers
the other extensions the organizer already accepts as video:

  AVI   RIFF INFO IDIT / ICRD text (wall-clock, no timezone)
  MKV   Matroska DateUTC (nanoseconds since 2001-01-01 UTC)
  WEBM  same Matroska element
  WMV   ASF File Properties creation time (Windows FILETIME, UTC)
  FLV   onMetaData creationdate (text, or an AMF date in UTC)

QuickTime, Matroska, and ASF timestamps are absolute instants. They are
returned as the UTC wall clock, not the machine's local zone: applying the
local zone files a clip from just before midnight into the next day or
month on a computer east of UTC. AVI text and FLV date strings are already
a wall clock and are taken as written.

Never raises.
"""

from __future__ import annotations

import os
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Matroska element IDs (the vint, including its length marker).
_ID_SEGMENT = 0x18538067
_ID_INFO = 0x1549A966
_ID_DATEUTC = 0x4461
_MATROSKA_DESCEND = frozenset({_ID_SEGMENT, _ID_INFO})

_ASF_HEADER = bytes.fromhex("3026B2758E66CF11A6D900AA0062CE6C")
_ASF_FILE_PROPS = bytes.fromhex("A1DCAB8C47A9CF118EE400C00C205365")
# Seconds between 1601-01-01 and 1970-01-01.
_FILETIME_TO_UNIX = 11644473600
_MATROSKA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def container_creation_date(path: Path, ext: str) -> Optional[datetime]:
    """Creation time for avi/mkv/webm/wmv/flv, or None if the file has none."""
    ext = ext.lower().lstrip(".")
    parser = {
        "avi": _avi_creation_date,
        "mkv": _matroska_creation_date,
        "webm": _matroska_creation_date,
        "wmv": _asf_creation_date,
        "flv": _flv_creation_date,
    }.get(ext)
    if parser is None:
        return None
    try:
        return parser(path)
    except Exception:
        return None


def _sane(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if dt.year < 1980 or dt.year > 2100:
        return None
    return dt


def _parse_wall_clock(text: str) -> Optional[datetime]:
    """Parse a container date string as a wall clock. Locale-independent."""
    text = text.strip().strip("\x00").strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y:%m:%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    # "Fri Jan 31 23:30:00 2020" or "Fri Jan  2 02:03:55 1990" (AVI IDIT).
    parts = text.split()
    if len(parts) >= 4 and parts[-1].isdigit() and len(parts[-1]) == 4:
        # drop a leading weekday
        if len(parts[0]) == 3 and parts[0].isalpha() and parts[1].isalpha():
            parts = parts[1:]
        if len(parts) == 4 and parts[0].lower() in _MONTHS:
            month = _MONTHS[parts[0].lower()]
            clock = parts[2].split(":")
            if len(clock) == 3 and parts[1].isdigit():
                try:
                    return datetime(
                        int(parts[3]), month, int(parts[1]),
                        int(clock[0]), int(clock[1]), int(clock[2]),
                    )
                except ValueError:
                    return None
    return None


# ---------------------------------------------------------------------------
# AVI (RIFF)
# ---------------------------------------------------------------------------

def _iter_riff(fh, end: int):
    while True:
        pos = fh.tell()
        if pos + 8 > end:
            return
        header = fh.read(8)
        if len(header) < 8:
            return
        cid = header[:4]
        (size,) = struct.unpack("<I", header[4:])
        data_pos = pos + 8
        nxt = data_pos + size + (size & 1)
        if size < 0 or nxt < data_pos or nxt > end + 1:
            return
        yield cid, data_pos, size
        fh.seek(nxt)


def _avi_search(fh, start: int, end: int, depth: int) -> Optional[datetime]:
    if depth > 8:
        return None
    fh.seek(start)
    for cid, data_pos, size in _iter_riff(fh, end):
        if cid == b"LIST":
            fh.seek(data_pos)
            kind = fh.read(4)
            if kind == b"movi":
                continue
            found = _avi_search(fh, data_pos + 4, data_pos + size, depth + 1)
            if found:
                return found
        elif cid in (b"IDIT", b"ICRD"):
            fh.seek(data_pos)
            raw = fh.read(min(size, 160))
            text = raw.split(b"\x00", 1)[0].decode("latin-1", "ignore")
            found = _sane(_parse_wall_clock(text))
            if found:
                return found
    return None


def _avi_creation_date(path: Path) -> Optional[datetime]:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        file_end = fh.tell()
        fh.seek(0)
        hdr = fh.read(12)
        if len(hdr) < 12 or hdr[:4] != b"RIFF" or hdr[8:12] != b"AVI ":
            return None
        return _avi_search(fh, 12, file_end, 0)


# ---------------------------------------------------------------------------
# Matroska / WebM
# ---------------------------------------------------------------------------

def _read_vint(fh, *, as_id: bool):
    first_b = fh.read(1)
    if not first_b:
        return None
    first = first_b[0]
    if first == 0:
        return None
    length = 1
    mask = 0x80
    while mask and (first & mask) == 0:
        length += 1
        mask >>= 1
    if length > 8:
        return None
    rest = fh.read(length - 1) if length > 1 else b""
    if len(rest) != length - 1:
        return None
    raw = int.from_bytes(bytes([first]) + rest, "big")
    if as_id:
        return raw
    value_bits = 7 * length
    value = raw & ((1 << value_bits) - 1)
    # All data bits set means "unknown size"; the caller cannot skip it.
    if value == (1 << value_bits) - 1:
        return None
    return value


def _matroska_date(payload: bytes) -> Optional[datetime]:
    if not payload or len(payload) > 8:
        return None
    if len(payload) < 8:
        pad = b"\xff" if payload[0] & 0x80 else b"\x00"
        payload = pad * (8 - len(payload)) + payload
    nanos = struct.unpack(">q", payload)[0]
    seconds, rem = divmod(nanos, 1_000_000_000)
    try:
        dt = _MATROSKA_EPOCH + timedelta(seconds=seconds, microseconds=rem // 1000)
    except OverflowError:
        return None
    return _sane(dt)


def _matroska_walk(fh, start: int, end: int, depth: int) -> Optional[datetime]:
    if depth > 8:
        return None
    fh.seek(start)
    while fh.tell() + 2 <= end:
        eid = _read_vint(fh, as_id=True)
        if eid is None:
            return None
        size = _read_vint(fh, as_id=False)
        if size is None:
            return None
        data_pos = fh.tell()
        data_end = data_pos + size
        if data_end < data_pos or data_end > end:
            return None
        if eid == _ID_DATEUTC:
            return _matroska_date(fh.read(min(size, 8)))
        if eid in _MATROSKA_DESCEND:
            found = _matroska_walk(fh, data_pos, data_end, depth + 1)
            if found:
                return found
        fh.seek(data_end)
    return None


def _matroska_creation_date(path: Path) -> Optional[datetime]:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        file_end = fh.tell()
        return _matroska_walk(fh, 0, file_end, 0)


# ---------------------------------------------------------------------------
# ASF / WMV
# ---------------------------------------------------------------------------

def _filetime_to_datetime(filetime: int) -> Optional[datetime]:
    if filetime <= 0:
        return None
    # Integer arithmetic: a float cannot represent a FILETIME exactly.
    unix = (filetime - _FILETIME_TO_UNIX * 10_000_000) // 10_000_000
    try:
        dt = datetime.fromtimestamp(unix, timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None
    return _sane(dt)


def _asf_creation_date(path: Path) -> Optional[datetime]:
    with open(path, "rb") as fh:
        guid = fh.read(16)
        if guid != _ASF_HEADER:
            return None
        size_b = fh.read(8)
        count_b = fh.read(4)
        reserved = fh.read(2)
        if len(size_b) < 8 or len(count_b) < 4 or len(reserved) < 2:
            return None
        (header_size,) = struct.unpack("<Q", size_b)
        (count,) = struct.unpack("<I", count_b)
        # header_size is relative to the start of the header object.
        file_end = fh.seek(0, os.SEEK_END)
        fh.seek(30)  # first child, after the 30-byte header-object preamble
        end = min(header_size, file_end)
        for _ in range(max(count, 1)):
            pos = fh.tell()
            if pos + 24 > end:
                return None
            child_guid = fh.read(16)
            (child_size,) = struct.unpack("<Q", fh.read(8))
            if child_size < 24 or pos + child_size > file_end:
                return None
            if child_guid == _ASF_FILE_PROPS:
                # File ID (16) + File Size (8) then Creation Date (8).
                fh.seek(pos + 24 + 16 + 8)
                raw = fh.read(8)
                if len(raw) < 8:
                    return None
                (filetime,) = struct.unpack("<Q", raw)
                return _filetime_to_datetime(filetime)
            fh.seek(pos + child_size)
    return None


# ---------------------------------------------------------------------------
# FLV onMetaData
# ---------------------------------------------------------------------------

def _amf_read(data: bytes, pos: int):
    if pos >= len(data):
        return None, pos
    kind = data[pos]
    pos += 1
    if kind == 0x02:  # string
        if pos + 2 > len(data):
            return None, len(data)
        (n,) = struct.unpack(">H", data[pos:pos + 2])
        pos += 2
        text = data[pos:pos + n].decode("utf-8", "ignore")
        return text, pos + n
    if kind == 0x00:  # number
        if pos + 8 > len(data):
            return None, len(data)
        (num,) = struct.unpack(">d", data[pos:pos + 8])
        return num, pos + 8
    if kind == 0x01:  # boolean
        if pos >= len(data):
            return None, len(data)
        return bool(data[pos]), pos + 1
    if kind in (0x05, 0x06):  # null, undefined
        return None, pos
    if kind == 0x0B:  # date: milliseconds since UTC epoch, then timezone minutes
        if pos + 10 > len(data):
            return None, len(data)
        (ms,) = struct.unpack(">d", data[pos:pos + 8])
        pos += 10
        try:
            dt = datetime.fromtimestamp(ms / 1000.0, timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None, pos
        return ("__date__", dt), pos
    if kind in (0x03, 0x08):  # object, ECMA array
        if kind == 0x08:
            if pos + 4 > len(data):
                return None, len(data)
            pos += 4  # approximate count; the end marker is authoritative
        obj = {}
        for _ in range(10000):
            if pos + 2 > len(data):
                break
            (n,) = struct.unpack(">H", data[pos:pos + 2])
            pos += 2
            if n == 0:
                if pos < len(data) and data[pos] == 0x09:
                    pos += 1
                break
            if pos + n > len(data):
                break
            key = data[pos:pos + n].decode("utf-8", "ignore")
            pos += n
            val, pos = _amf_read(data, pos)
            obj[key] = val
        return obj, pos
    return None, len(data)


def _date_from_metadata(obj: dict) -> Optional[datetime]:
    wanted = {"creationdate", "creation_date", "creationtime", "date"}
    for key, value in obj.items():
        if str(key).lower() not in wanted:
            continue
        if isinstance(value, str):
            return _sane(_parse_wall_clock(value))
        if isinstance(value, tuple) and value and value[0] == "__date__":
            return _sane(value[1])
        if isinstance(value, (int, float)) and value > 1_000_000_000:
            # Seconds, or milliseconds if the magnitude says so.
            seconds = value / 1000.0 if value > 10_000_000_000 else float(value)
            try:
                dt = datetime.fromtimestamp(seconds, timezone.utc).replace(tzinfo=None)
            except (OverflowError, OSError, ValueError):
                return None
            return _sane(dt)
    return None


def _flv_creation_date(path: Path) -> Optional[datetime]:
    with open(path, "rb") as fh:
        header = fh.read(9)
        if len(header) < 9 or header[:3] != b"FLV":
            return None
        (data_offset,) = struct.unpack(">I", header[5:9])
        fh.seek(data_offset)
        fh.read(4)  # previous tag size
        for _ in range(12):
            tag = fh.read(11)
            if len(tag) < 11:
                return None
            tag_type = tag[0]
            data_size = int.from_bytes(tag[1:4], "big")
            if data_size > 1_000_000:
                fh.seek(data_size + 4, os.SEEK_CUR)
                continue
            data = fh.read(data_size)
            fh.read(4)  # previous tag size
            if len(data) < data_size or tag_type != 18:
                continue
            _name, pos = _amf_read(data, 0)
            value, _pos = _amf_read(data, pos)
            if isinstance(value, dict):
                found = _date_from_metadata(value)
                if found:
                    return found
    return None
