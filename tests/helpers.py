"""Shared test helpers: build real media fixtures (JPEG+EXIF, synthetic MP4)."""

from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

from PIL import Image

from media_organizer.core.metadata import QT_EPOCH_OFFSET


# ---------------------------------------------------------------- JPEG/EXIF

def make_jpeg_with_exif(path: Path, dt: datetime | None) -> Path:
    """Create a real JPEG; when dt is given, write EXIF DateTimeOriginal."""
    img = Image.new("RGB", (8, 8), (120, 60, 200))
    if dt is None:
        img.save(path, "JPEG")
        return path
    exif = Image.Exif()
    stamp = dt.strftime("%Y:%m:%d %H:%M:%S")
    # DateTimeOriginal lives in the EXIF IFD (0x8769)
    exif.get_ifd(0x8769)
    exif[0x9003] = stamp  # DateTimeOriginal
    exif[0x9004] = stamp  # DateTimeDigitized
    exif[0x0132] = stamp  # DateTime
    img.save(path, "JPEG", exif=exif)
    return path


def _to_dms(value: float) -> tuple[float, float, float]:
    """Decimal degrees -> (deg, min, sec) floats for EXIF GPS rationals."""
    value = abs(value)
    deg = int(value)
    minutes = int((value - deg) * 60)
    seconds = round(((value - deg) * 60 - minutes) * 60, 4)
    return (float(deg), float(minutes), float(seconds))


def make_jpeg_with_gps(path: Path, dt: datetime | None,
                       lat: float, lon: float) -> Path:
    """Create a real JPEG with EXIF GPS coordinates (and optional date)."""
    img = Image.new("RGB", (8, 8), (40, 90, 160))
    exif = img.getexif()
    if dt is not None:
        stamp = dt.strftime("%Y:%m:%d %H:%M:%S")
        exif.get_ifd(0x8769)
        exif[0x9003] = stamp
    gps = exif.get_ifd(0x8825)
    gps[1] = "S" if lat < 0 else "N"
    gps[2] = _to_dms(lat)
    gps[3] = "W" if lon < 0 else "E"
    gps[4] = _to_dms(lon)
    img.save(path, "JPEG", exif=exif)
    return path


def make_png_with_text(path: Path, key: str, value: str) -> Path:
    from PIL.PngImagePlugin import PngInfo

    img = Image.new("RGB", (8, 8), (10, 120, 90))
    info = PngInfo()
    info.add_text(key, value)
    img.save(path, "PNG", pnginfo=info)
    return path


# ---------------------------------------------------------------- MP4 boxes

def _box(btype: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), btype) + payload


def make_mvhd(version: int, dt: datetime) -> bytes:
    qt_time = int(dt.timestamp()) + QT_EPOCH_OFFSET
    if version == 1:
        payload = struct.pack(">B3xQQI", 1, qt_time, qt_time, 1000)
    else:
        payload = struct.pack(">B3xIII", 0, qt_time, qt_time, 1000)
    payload += b"\x00" * 80  # rest of the box is irrelevant to the parser
    return _box(b"mvhd", payload)


def make_mp4(path: Path, dt: datetime, version: int = 0) -> Path:
    """Minimal MP4: ftyp + moov(mvhd)."""
    ftyp = _box(b"ftyp", b"isom\x00\x00\x00\x00isom")
    moov = _box(b"moov", make_mvhd(version, dt))
    path.write_bytes(ftyp + moov)
    return path


def make_mp4_with_track(path: Path, movie_dt: datetime, track_dt: datetime) -> Path:
    """MP4 where a track mdhd is older than the movie mvhd."""
    mdhd_payload = struct.pack(">B3xIII", 0,
                               int(track_dt.timestamp()) + QT_EPOCH_OFFSET,
                               int(track_dt.timestamp()) + QT_EPOCH_OFFSET, 1000)
    mdhd = _box(b"mdhd", mdhd_payload + b"\x00" * 8)
    trak = _box(b"trak", _box(b"mdia", mdhd))
    moov = _box(b"moov", make_mvhd(0, movie_dt) + trak)
    path.write_bytes(_box(b"ftyp", b"isom\x00\x00\x00\x00isom") + moov)
    return path


def make_mov_with_meta_date(path: Path, dt: datetime) -> Path:
    """MOV carrying a quicktime creationdate plist inside meta."""
    plist = (
        '<?xml version="1.0"?><plist><dict>'
        "<key>com.apple.quicktime.creationdate</key>"
        f"<string>{dt.strftime('%Y-%m-%dT%H:%M:%S')}Z</string>"
        "</dict></plist>"
    ).encode()
    meta = _box(b"meta", b"\x00\x00\x00\x00" + plist)
    moov = _box(b"moov", meta)
    path.write_bytes(_box(b"ftyp", b"qt  \x00\x00\x00\x00qt  ") + moov)
    return path
