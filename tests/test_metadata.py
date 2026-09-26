"""Tests for capture-date extraction (metadata.py)."""

from datetime import datetime

import pytest

from media_organizer.core.metadata import (
    Confidence, DateSource, extract_capture_date,
)
from tests.helpers import (
    make_jpeg_with_exif, make_mp4, make_mp4_with_track, make_mov_with_meta_date,
    make_png_with_text,
)


class TestExif:
    def test_datetimeoriginal_high_confidence(self, tmp_path):
        f = make_jpeg_with_exif(tmp_path / "photo.jpg", datetime(2021, 7, 4, 15, 30, 22))
        r = extract_capture_date(f)
        assert r.date == datetime(2021, 7, 4, 15, 30, 22)
        assert r.source == DateSource.EXIF
        assert r.confidence == Confidence.HIGH

    def test_jpeg_without_exif_falls_through(self, tmp_path):
        f = make_jpeg_with_exif(tmp_path / "IMG_20190302_101112.jpg", None)
        r = extract_capture_date(f)
        assert r.source == DateSource.FILENAME
        assert r.date == datetime(2019, 3, 2, 10, 11, 12)

    def test_corrupt_jpeg_never_raises(self, tmp_path):
        f = tmp_path / "broken.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 37)  # truncated JPEG
        r = extract_capture_date(f)
        assert r.source in (DateSource.MTIME, DateSource.NONE)


class TestPngText:
    def test_creation_time_chunk(self, tmp_path):
        f = make_png_with_text(tmp_path / "shot.png", "Creation Time",
                               "15 Jan 2022 09:08:07")
        r = extract_capture_date(f)
        assert r.source == DateSource.PNG_TEXT
        assert r.date == datetime(2022, 1, 15, 9, 8, 7)
        assert r.confidence == Confidence.HIGH

    def test_unrelated_text_chunk_ignored(self, tmp_path):
        f = make_png_with_text(tmp_path / "IMG_20201122_030405.png",
                               "Author", "someone")
        r = extract_capture_date(f)
        assert r.source == DateSource.FILENAME
        assert r.date == datetime(2020, 11, 22, 3, 4, 5)


class TestVideo:
    def test_mvhd_version0(self, tmp_path):
        f = make_mp4(tmp_path / "clip.mp4", datetime(2020, 5, 6, 12, 0, 0), version=0)
        r = extract_capture_date(f)
        assert r.source == DateSource.VIDEO
        assert r.date == datetime(2020, 5, 6, 12, 0, 0)

    def test_mvhd_version1(self, tmp_path):
        f = make_mp4(tmp_path / "clip.mov", datetime(2018, 11, 30, 23, 59, 1), version=1)
        r = extract_capture_date(f)
        assert r.source == DateSource.VIDEO
        assert r.date == datetime(2018, 11, 30, 23, 59, 1)

    def test_earliest_track_time_wins(self, tmp_path):
        f = make_mp4_with_track(tmp_path / "v.mp4",
                                datetime(2022, 1, 1), datetime(2019, 6, 15, 8, 0, 0))
        r = extract_capture_date(f)
        assert r.date == datetime(2019, 6, 15, 8, 0, 0)

    def test_mov_meta_creationdate(self, tmp_path):
        f = make_mov_with_meta_date(tmp_path / "qtime.mov", datetime(2017, 3, 9, 18, 45, 30))
        r = extract_capture_date(f)
        assert r.source == DateSource.VIDEO
        assert r.date == datetime(2017, 3, 9, 18, 45, 30)

    def test_zero_creation_time_ignored(self, tmp_path):
        # qt_time == 0 must not produce a 1904 date
        import struct
        from tests.helpers import _box
        payload = struct.pack(">B3xIII", 0, 0, 0, 1000) + b"\x00" * 80
        f = tmp_path / "zero.mp4"
        f.write_bytes(_box(b"ftyp", b"isom\x00\x00\x00\x00isom")
                      + _box(b"moov", _box(b"mvhd", payload)))
        r = extract_capture_date(f, include_mtime=False)
        assert r.source != DateSource.VIDEO

    def test_garbage_mp4_never_raises(self, tmp_path):
        f = tmp_path / "junk.mp4"
        f.write_bytes(b"not a real mp4 at all" * 20)
        r = extract_capture_date(f)
        assert r.source in (DateSource.MTIME, DateSource.NONE)


class TestFilenames:
    @pytest.mark.parametrize("name,expected", [
        ("IMG_20240115_123000.jpg", datetime(2024, 1, 15, 12, 30, 0)),
        ("VID_20231225_080000.mp4", datetime(2023, 12, 25, 8, 0, 0)),
        ("PXL_20221031_235959.jpg", datetime(2022, 10, 31, 23, 59, 59)),
        ("Screenshot_2024-01-15-12-30-00.png", datetime(2024, 1, 15, 12, 30, 0)),
        ("Screenshot_20240115-123000.png", datetime(2024, 1, 15, 12, 30, 0)),
        ("IMG-20240115-WA0001.jpg", datetime(2024, 1, 15)),
        ("VID-20200229-WA0042.mp4", datetime(2020, 2, 29)),
        ("2024-01-15 12.30.00.jpg", datetime(2024, 1, 15, 12, 30, 0)),
        ("2024-01-15_12-30-00.png", datetime(2024, 1, 15, 12, 30, 0)),
        ("2024-01-15 party.jpg", datetime(2024, 1, 15)),
        ("holidays 20190807.jpg", datetime(2019, 8, 7)),
    ])
    def test_patterns(self, tmp_path, name, expected):
        f = tmp_path / name
        f.write_bytes(b"\x00" * 10)
        r = extract_capture_date(f, include_mtime=False)
        assert r.source == DateSource.FILENAME
        assert r.confidence == Confidence.MEDIUM
        assert r.date == expected

    def test_no_date_in_name(self, tmp_path):
        f = tmp_path / "random_photo.jpg"
        f.write_bytes(b"\x00" * 10)
        r = extract_capture_date(f, include_mtime=False)
        assert not r.found
        assert r.source == DateSource.NONE

    def test_invalid_date_in_name_rejected(self, tmp_path):
        f = tmp_path / "IMG_20231345_999999.jpg"  # month 13 etc.
        f.write_bytes(b"\x00" * 10)
        r = extract_capture_date(f, include_mtime=False)
        assert not r.found


class TestMtimeFallback:
    def test_mtime_last_resort(self, tmp_path):
        import os
        f = tmp_path / "nodate.bin.jpg"
        f.write_bytes(b"\x00" * 10)
        ts = datetime(2020, 2, 3, 4, 5, 6).timestamp()
        os.utime(f, (ts, ts))
        r = extract_capture_date(f)
        assert r.source == DateSource.MTIME
        assert r.confidence == Confidence.LOW
        assert r.date == datetime(2020, 2, 3, 4, 5, 6)

    def test_missing_file_returns_none(self, tmp_path):
        r = extract_capture_date(tmp_path / "ghost.jpg", include_mtime=True)
        assert not r.found


class TestFallbackOrdering:
    def test_exif_beats_filename(self, tmp_path):
        # Filename says 2010, EXIF says 2021 -> EXIF must win
        f = make_jpeg_with_exif(tmp_path / "IMG_20100101_000000.jpg",
                                datetime(2021, 7, 4, 15, 30, 22))
        r = extract_capture_date(f)
        assert r.source == DateSource.EXIF
        assert r.date == datetime(2021, 7, 4, 15, 30, 22)

    def test_video_meta_beats_filename(self, tmp_path):
        f = make_mp4(tmp_path / "VID_20050101_000000.mp4",
                     datetime(2020, 5, 6, 12, 0, 0), version=0)
        r = extract_capture_date(f)
        assert r.source == DateSource.VIDEO
        assert r.date == datetime(2020, 5, 6, 12, 0, 0)
