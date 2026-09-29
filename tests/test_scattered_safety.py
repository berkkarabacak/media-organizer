"""Regressions for organizing a messy folder without losing files or dates.

These tests are timezone-independent where a clock time is the point: they
force a non-UTC zone (or build container timestamps from UTC integers) so a
parser that round-trips through the machine's local timezone cannot pass.
"""

from __future__ import annotations

import calendar
import os
import struct
import time
from datetime import datetime, timezone

import pytest

from media_organizer.core.executor import execute_plan
from media_organizer.core.journal import PART_SUFFIX, cleanup_stale_parts
from media_organizer.core.metadata import (
    QT_EPOCH_OFFSET, DateSource, extract_capture_date,
)
from media_organizer.core.organizer import OrganizeOptions, build_plan, scan_media_files
from media_organizer.core.plan import load_log, undo_log
from tests.helpers import _box, make_jpeg_with_exif


# Pacific/Kiritimati is UTC+14: 2020-01-31 23:30 UTC is 2020-02-01 13:30 there.
_FAR_EAST = "Pacific/Kiritimati"
_UTC_NEAR_MIDNIGHT = datetime(2020, 1, 31, 23, 30, 0)
_UNIX_NEAR_MIDNIGHT = calendar.timegm(_UTC_NEAR_MIDNIGHT.timetuple())


@pytest.fixture
def far_east_tz():
    """Force a timezone where local midnight is not UTC midnight."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is unavailable; cannot force a timezone")
    old = os.environ.get("TZ")
    os.environ["TZ"] = _FAR_EAST
    time.tzset()
    local = datetime.fromtimestamp(_UNIX_NEAR_MIDNIGHT)
    if local == _UTC_NEAR_MIDNIGHT:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()
        pytest.skip(f"TZ={_FAR_EAST} did not change local time; zone data missing?")
    try:
        yield local
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def _mp4_with_raw_qt(path, qt_time: int, version: int = 0):
    if version == 1:
        payload = struct.pack(">B3xQQI", 1, qt_time, qt_time, 1000)
    else:
        payload = struct.pack(">B3xIII", 0, qt_time, qt_time, 1000)
    payload += b"\x00" * 80
    path.write_bytes(
        _box(b"ftyp", b"isom\x00\x00\x00\x00isom")
        + _box(b"moov", _box(b"mvhd", payload))
    )
    return path


def _riff_chunk(cid: bytes, data: bytes) -> bytes:
    pad = b"\x00" if len(data) % 2 else b""
    return cid + struct.pack("<I", len(data)) + data + pad


def _avi_with_idit(path, text: str):
    info = _riff_chunk(b"LIST", b"INFO" + _riff_chunk(b"IDIT", text.encode("ascii") + b"\x00"))
    movi = _riff_chunk(b"LIST", b"movi" + b"\x00" * 64)
    body = info + movi
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"AVI " + body)
    return path


def _vint_size(n: int) -> bytes:
    if n < 0x7F:
        return bytes([0x80 | n])
    if n < 0x3FFF:
        return struct.pack(">H", 0x4000 | n)
    raise AssertionError(f"test element too large: {n}")


def _ebml(element_id: bytes, payload: bytes) -> bytes:
    return element_id + _vint_size(len(payload)) + payload


def _matroska_with_date(path, when: datetime):
    """Minimal Matroska/WebM Segment whose Info/DateUTC is `when` as UTC."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    epoch = datetime(2001, 1, 1, tzinfo=timezone.utc)
    nanos = int((when - epoch).total_seconds() * 1_000_000_000)
    date = _ebml(bytes.fromhex("4461"), struct.pack(">q", nanos))
    # A non-Info element in front: the parser must seek past it, not assume
    # DateUTC is the first thing in the segment.
    dummy = _ebml(bytes.fromhex("4123"), b"\x00" * 40)
    info = _ebml(bytes.fromhex("1549A966"), date)
    segment = _ebml(bytes.fromhex("18538067"), dummy + info)
    path.write_bytes(segment)
    return path


def _asf_with_filetime(path, when: datetime):
    header_guid = bytes.fromhex("3026B2758E66CF11A6D900AA0062CE6C")
    props_guid = bytes.fromhex("A1DCAB8C47A9CF118EE400C00C205365")
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    unix = int(when.timestamp())
    filetime = unix * 10_000_000 + 11644473600 * 10_000_000
    props = (
        b"\x11" * 16
        + struct.pack("<Q", 100)
        + struct.pack("<Q", filetime)
        + struct.pack("<Q", 0) * 4
        + struct.pack("<IIII", 0, 0, 0, 0)
    )
    child_size = 24 + len(props)
    child = props_guid + struct.pack("<Q", child_size) + props
    rest = struct.pack("<I", 1) + b"\x01\x02" + child
    header_size = 16 + 8 + len(rest)
    path.write_bytes(header_guid + struct.pack("<Q", header_size) + rest)
    return path


def _amf_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def _flv_with_creation(path, value: bytes):
    """FLV onMetaData script tag. `value` is a complete AMF0 value."""
    props = _amf_string("creationdate") + value
    script = (
        b"\x02" + _amf_string("onMetaData")
        + b"\x08" + struct.pack(">I", 1) + props + b"\x00\x00\x09"
    )
    data_size = len(script)
    tag = (
        bytes([18])
        + data_size.to_bytes(3, "big")
        + b"\x00\x00\x00\x00\x00\x00\x00"
        + script
    )
    header = b"FLV" + bytes([1, 0]) + struct.pack(">I", 9)
    path.write_bytes(header + struct.pack(">I", 0) + tag)
    return path


class TestVideoClockIsNotLocal:
    def test_mp4_mvhd_stays_on_utc_day(self, tmp_path, far_east_tz):
        qt = _UNIX_NEAR_MIDNIGHT + QT_EPOCH_OFFSET
        f = _mp4_with_raw_qt(tmp_path / "clip.mp4", qt, version=0)
        result = extract_capture_date(f, include_mtime=False)
        assert result.source == DateSource.VIDEO
        assert result.date == _UTC_NEAR_MIDNIGHT
        # The zone fixture's local reading is the next month. That must not win.
        assert result.date != far_east_tz
        assert result.date.month == 1

    def test_mov_mvhd_version1_stays_on_utc_day(self, tmp_path, far_east_tz):
        qt = _UNIX_NEAR_MIDNIGHT + QT_EPOCH_OFFSET
        f = _mp4_with_raw_qt(tmp_path / "clip.mov", qt, version=1)
        result = extract_capture_date(f, include_mtime=False)
        assert result.source == DateSource.VIDEO
        assert result.date == _UTC_NEAR_MIDNIGHT

    def test_exif_clock_time_does_not_follow_timezone(self, tmp_path, far_east_tz):
        f = make_jpeg_with_exif(
            tmp_path / "photo.jpg", datetime(2020, 1, 31, 23, 30, 0))
        result = extract_capture_date(f)
        assert result.source == DateSource.EXIF
        assert result.date == datetime(2020, 1, 31, 23, 30, 0)
        assert result.date != far_east_tz


class TestOtherVideoContainers:
    def test_avi_idit_ctime(self, tmp_path, far_east_tz):
        f = _avi_with_idit(tmp_path / "tape.avi", "Fri Jan 31 23:30:00 2020")
        result = extract_capture_date(f, include_mtime=False)
        assert result.source == DateSource.VIDEO
        assert result.date == _UTC_NEAR_MIDNIGHT

    def test_avi_idit_numeric(self, tmp_path):
        f = _avi_with_idit(tmp_path / "tape.avi", "2020-01-31 23:30:00")
        result = extract_capture_date(f, include_mtime=False)
        assert result.source == DateSource.VIDEO
        assert result.date == _UTC_NEAR_MIDNIGHT

    def test_avi_without_date_still_uses_filename(self, tmp_path):
        f = tmp_path / "VID_20240115_123000.avi"
        f.write_bytes(b"RIFF" + struct.pack("<I", 4) + b"AVI ")
        result = extract_capture_date(f, include_mtime=False)
        assert result.source == DateSource.FILENAME
        assert result.date == datetime(2024, 1, 15, 12, 30, 0)

    def test_mkv_dateutc_is_utc(self, tmp_path, far_east_tz):
        f = _matroska_with_date(tmp_path / "film.mkv", _UTC_NEAR_MIDNIGHT)
        result = extract_capture_date(f, include_mtime=False)
        assert result.source == DateSource.VIDEO
        assert result.date == _UTC_NEAR_MIDNIGHT
        assert result.date != far_east_tz

    def test_webm_dateutc_is_utc(self, tmp_path, far_east_tz):
        f = _matroska_with_date(tmp_path / "film.webm", _UTC_NEAR_MIDNIGHT)
        result = extract_capture_date(f, include_mtime=False)
        assert result.source == DateSource.VIDEO
        assert result.date == _UTC_NEAR_MIDNIGHT

    def test_wmv_filetime_is_utc(self, tmp_path, far_east_tz):
        f = _asf_with_filetime(tmp_path / "clip.wmv", _UTC_NEAR_MIDNIGHT)
        result = extract_capture_date(f, include_mtime=False)
        assert result.source == DateSource.VIDEO
        assert result.date == _UTC_NEAR_MIDNIGHT
        assert result.date != far_east_tz

    def test_flv_onmetadata_string(self, tmp_path, far_east_tz):
        value = b"\x02" + _amf_string("2020-01-31 23:30:00")
        f = _flv_with_creation(tmp_path / "clip.flv", value)
        result = extract_capture_date(f, include_mtime=False)
        assert result.source == DateSource.VIDEO
        assert result.date == _UTC_NEAR_MIDNIGHT

    def test_flv_onmetadata_amf_date_is_utc(self, tmp_path, far_east_tz):
        ms = float(_UNIX_NEAR_MIDNIGHT * 1000)
        value = b"\x0b" + struct.pack(">d", ms) + struct.pack(">h", 0)
        f = _flv_with_creation(tmp_path / "clip.flv", value)
        result = extract_capture_date(f, include_mtime=False)
        assert result.source == DateSource.VIDEO
        assert result.date == _UTC_NEAR_MIDNIGHT
        assert result.date != far_east_tz

    def test_garbage_containers_do_not_raise(self, tmp_path):
        for name in ("x.avi", "x.mkv", "x.webm", "x.wmv", "x.flv"):
            f = tmp_path / name
            f.write_bytes(b"this is not a real container" * 3)
            result = extract_capture_date(f, include_mtime=False)
            assert result.source != DateSource.VIDEO


class TestDestinationOverlap:
    def test_same_folder_scans_nothing_and_explains(self, tmp_path):
        from media_organizer.core.organizer import destination_blocks_scan

        src = tmp_path / "photos"
        src.mkdir()
        (src / "a.jpg").write_bytes(b"jpeg")
        reason = destination_blocks_scan(src, src)
        assert reason
        assert "same folder" in reason.lower()
        found = list(scan_media_files(OrganizeOptions(source_dir=src, dest_dir=src)))
        assert found == []

    def test_parent_destination_scans_nothing_and_explains(self, tmp_path):
        from media_organizer.core.organizer import destination_blocks_scan

        parent = tmp_path / "photos"
        src = parent / "messy"
        src.mkdir(parents=True)
        (src / "a.jpg").write_bytes(b"jpeg")
        (src / "sub").mkdir()
        (src / "sub" / "b.jpg").write_bytes(b"jpeg")
        reason = destination_blocks_scan(src, parent)
        assert reason
        assert "parent" in reason.lower()
        found = list(scan_media_files(
            OrganizeOptions(source_dir=src, dest_dir=parent)))
        assert found == []

    def test_destination_inside_source_still_scans(self, tmp_path):
        from media_organizer.core.organizer import destination_blocks_scan

        src = tmp_path / "photos"
        dst = src / "Organized"
        src.mkdir()
        (src / "a.jpg").write_bytes(b"jpeg")
        assert destination_blocks_scan(src, dst) is None
        found = list(scan_media_files(OrganizeOptions(source_dir=src, dest_dir=dst)))
        assert [p.name for p in found] == ["a.jpg"]

    def test_symlink_to_source_is_the_same_folder(self, tmp_path):
        from media_organizer.core.organizer import destination_blocks_scan

        src = tmp_path / "photos"
        src.mkdir()
        (src / "a.jpg").write_bytes(b"jpeg")
        link = tmp_path / "alias"
        link.symlink_to(src, target_is_directory=True)
        reason = destination_blocks_scan(src, link)
        assert reason
        assert "same folder" in reason.lower()


class TestPartCleanup:
    def test_user_part_files_are_kept_app_parts_are_removed(self, tmp_path):
        (tmp_path / "notes.part").write_bytes(b"user notes")
        (tmp_path / "clip.mp4.part").write_bytes(b"user video fragment")
        app_part = tmp_path / "nested" / f"photo.jpg{PART_SUFFIX}"
        app_part.parent.mkdir()
        app_part.write_bytes(b"incomplete copy")
        (tmp_path / "keep.jpg").write_bytes(b"photo")

        removed = cleanup_stale_parts(tmp_path)

        assert (tmp_path / "notes.part").read_bytes() == b"user notes"
        assert (tmp_path / "clip.mp4.part").read_bytes() == b"user video fragment"
        assert (tmp_path / "keep.jpg").read_bytes() == b"photo"
        assert not app_part.exists()
        assert removed == 1


def _one_photo_run(tmp_path, *, copy_mode=True):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    photo = make_jpeg_with_exif(src / "IMG_1.jpg", datetime(2024, 7, 15, 10, 30, 0))
    options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=copy_mode)
    plan = build_plan(options)
    execute_plan(plan, options)
    copied = dst / "2024" / "07 July" / "IMG_1.jpg"
    return src, dst, photo, copied


class TestUndoDoesNotDestroyTheOnlyCopy:
    def test_copy_undo_keeps_file_when_original_is_gone(self, tmp_path):
        _src, dst, photo, copied = _one_photo_run(tmp_path)
        original = photo.read_bytes()
        photo.unlink()
        result = undo_log(load_log(dst), dst)
        assert copied.exists()
        assert copied.read_bytes() == original
        assert result["undone"] == 0
        assert result["kept"] == 1

    def test_copy_undo_keeps_file_that_no_longer_matches(self, tmp_path):
        _src, dst, _photo, copied = _one_photo_run(tmp_path)
        copied.write_bytes(b"edited-by-the-user-not-the-original-bytes")
        result = undo_log(load_log(dst), dst)
        assert copied.read_bytes().startswith(b"edited-by-the-user")
        assert result["undone"] == 0
        assert result["kept"] == 1

    def test_copy_undo_does_not_unlink_when_source_is_destination(self, tmp_path):
        photo = tmp_path / "IMG_1.jpg"
        photo.write_bytes(b"only-copy")
        from media_organizer.core.plan import Operation, RunLog, new_log, save_log
        log = new_log(tmp_path)
        log.operations.append(Operation(
            action="copy", source=str(photo), destination=str(photo),
            status="done", size=len(b"only-copy"), sha256=""))
        save_log(log, tmp_path)
        result = undo_log(load_log(tmp_path), tmp_path)
        assert photo.read_bytes() == b"only-copy"
        assert result["kept"] == 1
        assert result["undone"] == 0

    def test_copy_undo_removes_duplicate_when_original_matches(self, tmp_path):
        _src, dst, photo, copied = _one_photo_run(tmp_path)
        result = undo_log(load_log(dst), dst)
        assert result["undone"] == 1
        assert not copied.exists()
        assert photo.exists()

    def test_copy_undo_prefers_recycle_bin(self, tmp_path, monkeypatch):
        import media_organizer.core.plan as plan_mod

        recycled = []

        def fake_recycle(path):
            recycled.append(path)
            return True

        monkeypatch.setattr(plan_mod, "_send_to_recycle_bin", fake_recycle)
        _src, dst, _photo, copied = _one_photo_run(tmp_path)
        result = undo_log(load_log(dst), dst)
        assert recycled == [copied]
        assert copied.exists()  # recycle reported success; do not also hard-delete
        assert result["undone"] == 1

    def test_move_undo_puts_the_same_file_back(self, tmp_path):
        _src, dst, photo, moved = _one_photo_run(tmp_path, copy_mode=False)
        payload = moved.read_bytes()
        result = undo_log(load_log(dst), dst)
        assert result["undone"] == 1
        assert result["kept"] == 0
        assert photo.read_bytes() == payload
        assert not moved.exists()

    def test_move_undo_does_not_relocate_a_different_file(self, tmp_path):
        _src, dst, photo, moved = _one_photo_run(tmp_path, copy_mode=False)
        assert not photo.exists()
        assert moved.exists()
        # Same length as the file this run wrote, different bytes — a size
        # check alone would not notice the swap.
        original = moved.read_bytes()
        moved.write_bytes(bytes(b ^ 0xFF for b in original))
        result = undo_log(load_log(dst), dst)
        assert moved.read_bytes() != original
        assert not photo.exists()
        assert result["kept"] == 1
        assert result["undone"] == 0

    def test_undoing_the_latest_run_leaves_the_earlier_copy(self, tmp_path):
        """A second organize replaces the only undo log.

        Undoing that log must not delete the copy the first run wrote.
        There is no history to undo the first run; the UI says so.
        """
        src, dst, _photo, first = _one_photo_run(tmp_path)
        make_jpeg_with_exif(src / "IMG_2.jpg", datetime(2024, 8, 1, 9, 0, 0))
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        execute_plan(build_plan(options), options)
        second = dst / "2024" / "07 July" / "IMG_1_1.jpg"
        assert first.exists() and second.exists()
        undo_log(load_log(dst), dst)
        assert first.exists()
        assert not second.exists()


class TestRunWording:
    def test_mtime_files_are_counted_as_uncertain(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        mystery = src / "mystery.jpg"
        mystery.write_bytes(b"\xff\xd8\xff\xd9")
        ts = datetime(2019, 4, 2, 1, 2, 3).timestamp()
        os.utime(mystery, (ts, ts))
        options = OrganizeOptions(source_dir=src, dest_dir=dst)  # uncertain=aside
        plan = build_plan(options)
        assert plan[0].destination.parent.name == "_uncertain"
        _log, summary = execute_plan(plan, options)
        assert summary.get("uncertain") == 1
        assert summary.get("undated") == 0

    def test_move_dry_run_is_a_move(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        make_jpeg_with_exif(src / "IMG_1.jpg", datetime(2024, 7, 15, 10, 0, 0))
        options = OrganizeOptions(source_dir=src, dest_dir=dst,
                                  copy_mode=False, dry_run=True)
        _log, summary = execute_plan(build_plan(options), options)
        assert summary["moved"] == 1
        assert summary["copied"] == 0
        assert summary.get("action") == "move"
        assert not dst.exists() or not any(dst.rglob("*.jpg"))


class TestWindowsJunctions:
    @pytest.mark.skipif(
        os.name != "nt",
        reason=(
            "Windows directory junctions cannot be created on this machine "
            "(no mklink /J). Not verified, and no scan change was made for it: "
            "scan_media_files compares os.walk paths to the resolved destination "
            "string, while os.walk follows junctions on some Windows Python "
            "versions even with followlinks left at the default. A junction "
            "used as the destination inside the source, or a junction that "
            "loops back into the source, could be scanned, skipped entirely, "
            "or walked without end. This skip is not a pass."
        ),
    )
    def test_junction_destination_inside_source_is_pruned(self, tmp_path):
        import subprocess

        src = tmp_path / "photos"
        target = tmp_path / "elsewhere"
        link = src / "Organized"
        src.mkdir()
        target.mkdir()
        (src / "a.jpg").write_bytes(b"jpeg")
        (target / "already.jpg").write_bytes(b"jpeg")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True)
        found = list(scan_media_files(
            OrganizeOptions(source_dir=src, dest_dir=link)))
        names = sorted(p.name for p in found)
        assert names == ["a.jpg"]
