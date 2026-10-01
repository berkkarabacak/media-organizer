"""Regressions for organizing a messy folder without losing files or dates.

These tests are timezone-independent where a clock time is the point: they
force a non-UTC zone (or build container timestamps from UTC integers) so a
parser that round-trips through the machine's local timezone cannot pass.
"""

from __future__ import annotations

import calendar
import json
import os
import stat
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from media_organizer.core.executor import execute_plan
from media_organizer.core.journal import (
    PART_SUFFIX, JournalWriter, cleanup_stale_parts, find_unfinished_journal,
)
from media_organizer.core.metadata import (
    QT_EPOCH_OFFSET, DateSource, extract_capture_date,
)
from media_organizer.core.organizer import (
    OrganizeOptions, build_plan, count_media_files, scan_media_files,
    _is_non_descendable_dir, _stat_is_non_descendable_dir,
)
from media_organizer.core.plan import (
    UNDO_LIMITATION, UNDO_STACK_LIMIT, Operation, list_run_logs, load_log,
    load_undoable_log, log_path_for, new_log, save_log, undo_log,
    undo_result_message,
)
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
        options = OrganizeOptions(source_dir=src, dest_dir=src)
        found = list(scan_media_files(options))
        assert found == []
        assert count_media_files(options) == 0

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
        options = OrganizeOptions(source_dir=src, dest_dir=parent)
        found = list(scan_media_files(options))
        assert found == []
        assert count_media_files(options) == 0

    def test_destination_inside_source_still_scans(self, tmp_path):
        from media_organizer.core.organizer import destination_blocks_scan

        src = tmp_path / "photos"
        dst = src / "Organized"
        src.mkdir()
        dst.mkdir()
        (src / "a.jpg").write_bytes(b"jpeg")
        (dst / "already.jpg").write_bytes(b"jpeg")
        assert destination_blocks_scan(src, dst) is None
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        found = list(scan_media_files(options))
        assert [p.name for p in found] == ["a.jpg"]
        assert count_media_files(options) == 1

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
    def test_user_part_files_are_kept_orphan_app_parts_are_deleted(self, tmp_path):
        (tmp_path / "notes.part").write_bytes(b"user notes")
        (tmp_path / "clip.mp4.part").write_bytes(b"user video fragment")
        app_part = tmp_path / "nested" / f"photo.jpg{PART_SUFFIX}"
        app_part.parent.mkdir()
        app_part.write_bytes(b"incomplete copy")
        (tmp_path / "keep.jpg").write_bytes(b"photo")

        resolved = cleanup_stale_parts(tmp_path)

        assert (tmp_path / "notes.part").read_bytes() == b"user notes"
        assert (tmp_path / "clip.mp4.part").read_bytes() == b"user video fragment"
        assert (tmp_path / "keep.jpg").read_bytes() == b"photo"
        assert not (app_part.parent / "photo.jpg").exists()
        assert not app_part.exists()
        assert resolved == 1


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
        """Undoing the latest run removes only that run's copies.

        The first run's destination and its undo log both stay.
        """
        src, dst, _photo, first = _one_photo_run(tmp_path)
        make_jpeg_with_exif(src / "IMG_2.jpg", datetime(2024, 8, 1, 9, 0, 0))
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        execute_plan(build_plan(options), options)
        second = dst / "2024" / "07 July" / "IMG_1_1.jpg"
        assert first.exists() and second.exists()
        earlier_id = list_run_logs(dst)[1].run_id
        undo_log(load_log(dst), dst)
        assert first.exists()
        assert not second.exists()
        still = [log for log in list_run_logs(dst) if log.run_id == earlier_id]
        assert len(still) == 1 and not still[0].undone


class TestUndoHistory:
    def test_second_run_archives_the_first_log_unchanged(self, tmp_path):
        src, dst, _photo, first = _one_photo_run(tmp_path)
        original = log_path_for(dst).read_bytes()
        assert original
        make_jpeg_with_exif(src / "IMG_2.jpg", datetime(2024, 8, 1, 9, 0, 0))
        execute_plan(build_plan(OrganizeOptions(source_dir=src, dest_dir=dst)),
                     OrganizeOptions(source_dir=src, dest_dir=dst))
        archived = list((dst / ".media_organizer" / "history").glob("*.json"))
        assert len(archived) == 1
        assert archived[0].read_bytes() == original
        logs = list_run_logs(dst)
        assert len(logs) == 2
        assert logs[1].operations[0].destination == str(first)
        assert Path(logs[0].operations[0].destination) != first

    def test_legacy_log_without_run_id_is_not_wiped(self, tmp_path):
        src, dst, _photo, first = _one_photo_run(tmp_path)
        path = log_path_for(dst)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("run_id", None)
        original = json.dumps(data)
        path.write_text(original, encoding="utf-8")
        make_jpeg_with_exif(src / "IMG_2.jpg", datetime(2024, 8, 1, 9, 0, 0))
        execute_plan(build_plan(OrganizeOptions(source_dir=src, dest_dir=dst)),
                     OrganizeOptions(source_dir=src, dest_dir=dst))
        archived = list((dst / ".media_organizer" / "history").glob("*.json"))
        assert len(archived) == 1
        assert archived[0].read_text(encoding="utf-8") == original
        older = list_run_logs(dst)[-1]
        assert older.run_id == ""
        assert not older.undone
        assert older.operations[0].destination == str(first)

    def test_undo_latest_then_the_earlier_run(self, tmp_path):
        src, dst, photo, first = _one_photo_run(tmp_path)
        make_jpeg_with_exif(src / "IMG_2.jpg", datetime(2024, 8, 1, 9, 0, 0))
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        execute_plan(build_plan(options), options)
        second = dst / "2024" / "08 August" / "IMG_2.jpg"
        recopy = dst / "2024" / "07 July" / "IMG_1_1.jpg"
        assert first.exists() and second.exists() and recopy.exists()
        history = next((dst / ".media_organizer" / "history").glob("*.json"))
        preserved = history.read_bytes()

        latest = undo_log(load_log(dst), dst)
        assert latest["undone"] >= 1
        assert latest["remaining"] == 1
        assert first.exists()
        assert not second.exists()
        assert not recopy.exists()
        assert history.read_bytes() == preserved

        earlier = load_undoable_log(dst)
        assert earlier is not None and not earlier.undone
        again = undo_log(earlier, dst)
        assert again["undone"] == 1
        assert again["remaining"] == 0
        assert not first.exists()
        assert photo.exists()
        assert (src / "IMG_2.jpg").exists()

    def test_undo_older_run_while_the_newer_one_remains(self, tmp_path):
        src, dst, photo, first = _one_photo_run(tmp_path)
        make_jpeg_with_exif(src / "IMG_2.jpg", datetime(2024, 8, 1, 9, 0, 0))
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        execute_plan(build_plan(options), options)
        logs = list_run_logs(dst)
        newer, older = logs[0], logs[1]
        newer_dests = [Path(op.destination) for op in newer.operations
                       if op.status == "done"]
        assert newer_dests
        result = undo_log(older, dst)
        assert result["undone"] == 1
        assert result["remaining"] == 1
        assert not first.exists()
        assert photo.exists()
        for path in newer_dests:
            assert path.exists()
        reloaded = list_run_logs(dst)
        assert len(reloaded) == 2
        assert reloaded[0].run_id == newer.run_id
        assert not reloaded[0].undone
        assert [op.destination for op in reloaded[0].operations] == \
            [op.destination for op in newer.operations]
        assert reloaded[1].undone

    def test_undo_latest_move_leaves_the_earlier_move(self, tmp_path):
        src, dst, photo, first = _one_photo_run(tmp_path, copy_mode=False)
        payload = first.read_bytes()
        assert not photo.exists() and first.exists()
        photo2 = make_jpeg_with_exif(
            src / "IMG_2.jpg", datetime(2024, 8, 1, 9, 0, 0))
        options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False)
        execute_plan(build_plan(options), options)
        second = dst / "2024" / "08 August" / "IMG_2.jpg"
        assert second.exists() and not photo2.exists()

        result = undo_log(load_log(dst), dst)
        assert result["undone"] == 1
        assert result["remaining"] == 1
        assert first.read_bytes() == payload
        assert not photo.exists()
        assert photo2.read_bytes()
        assert not second.exists()

        earlier = load_undoable_log(dst)
        assert earlier is not None
        again = undo_log(earlier, dst)
        assert again["undone"] == 1
        assert not first.exists()
        assert photo.read_bytes() == payload

    def test_filling_the_stack_retires_the_oldest_log_without_deleting_it(
            self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        first_bytes = b""
        first_id = ""
        for i in range(UNDO_STACK_LIMIT + 1):
            make_jpeg_with_exif(
                src / f"IMG_{i}.jpg", datetime(2020, 1, 1, 0, i, 0))
            options = OrganizeOptions(source_dir=src, dest_dir=dst)
            execute_plan(build_plan(options), options)
            if i == 0:
                first_bytes = log_path_for(dst).read_bytes()
                first_id = load_log(dst).run_id
            if i + 1 == UNDO_STACK_LIMIT:
                assert len(list_run_logs(dst)) == UNDO_STACK_LIMIT
                retired_dir = dst / ".media_organizer" / "history" / "retired"
                assert not retired_dir.exists()
        logs = list_run_logs(dst)
        assert len(logs) == UNDO_STACK_LIMIT
        assert first_id not in {log.run_id for log in logs}
        retired = list(
            (dst / ".media_organizer" / "history" / "retired").glob("*.json"))
        assert len(retired) == 1
        assert retired[0].read_bytes() == first_bytes
        assert retired[0].stat().st_size > 0


class TestKeptUndoAdvancesTheStack:
    """A run that must leave files in place does not pin Undo last run."""

    def test_kept_newer_run_then_older_run_can_be_undone(self, tmp_path):
        src, dst, photo, first = _one_photo_run(tmp_path)
        photo2 = make_jpeg_with_exif(
            src / "IMG_2.jpg", datetime(2024, 8, 1, 9, 0, 0))
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        execute_plan(build_plan(options), options)
        second = dst / "2024" / "08 August" / "IMG_2.jpg"
        recopy = dst / "2024" / "07 July" / "IMG_1_1.jpg"
        assert first.exists() and second.exists() and recopy.exists()
        photo2.unlink()

        latest = load_undoable_log(dst)
        assert latest is not None
        result = undo_log(latest, dst)
        assert result["kept"] >= 1
        assert result["closed"] is True
        assert result["remaining"] == 1
        assert second.exists()
        assert not recopy.exists()
        assert first.exists()

        saved_latest = next(
            log for log in list_run_logs(dst) if log.run_id == latest.run_id)
        assert saved_latest.undone
        kept_ops = [op for op in saved_latest.operations if op.status == "kept"]
        assert [Path(op.destination) for op in kept_ops] == [second]

        earlier = load_undoable_log(dst)
        assert earlier is not None
        assert earlier.run_id != latest.run_id
        assert not earlier.undone
        again = undo_log(earlier, dst)
        assert again["undone"] == 1
        assert again["kept"] == 0
        assert again["closed"] is True
        assert again["remaining"] == 0
        assert not first.exists()
        assert photo.exists()
        assert second.exists()
        assert load_undoable_log(dst) is None

    def test_identical_original_copy_is_removed(self, tmp_path):
        _src, dst, photo, copied = _one_photo_run(tmp_path)
        result = undo_log(load_undoable_log(dst), dst)
        assert result["undone"] == 1
        assert result["kept"] == 0
        assert result["failed"] == 0
        assert result["closed"] is True
        assert not copied.exists()
        assert photo.is_file()
        assert load_undoable_log(dst) is None

    @pytest.mark.parametrize("how", ["missing", "changed_original", "changed_copy"])
    def test_copy_stays_when_original_is_missing_or_different(self, tmp_path, how):
        _src, dst, photo, copied = _one_photo_run(tmp_path)
        original = copied.read_bytes()
        if how == "missing":
            photo.unlink()
        elif how == "changed_original":
            photo.write_bytes(b"not-the-copy-anymore")
        else:
            copied.write_bytes(b"user-edit-of-the-organized-copy")
        result = undo_log(load_undoable_log(dst), dst)
        assert result["kept"] == 1
        assert result["undone"] == 0
        assert result["closed"] is True
        assert copied.exists()
        if how == "changed_copy":
            assert copied.read_bytes().startswith(b"user-edit")
        else:
            assert copied.read_bytes() == original
        if how != "missing":
            assert photo.exists()
        saved = load_log(dst)
        assert saved is not None and saved.undone
        assert saved.operations[0].status == "kept"
        assert load_undoable_log(dst) is None

    @pytest.mark.parametrize("how", ["size", "sha256"])
    def test_move_undo_refuses_when_size_or_sha256_changes(self, tmp_path, how):
        _src, dst, photo, moved = _one_photo_run(tmp_path, copy_mode=False)
        original = moved.read_bytes()
        assert not photo.exists()
        if how == "size":
            moved.write_bytes(original + b"x")
        else:
            moved.write_bytes(bytes(b ^ 0xFF for b in original))
            assert moved.stat().st_size == len(original)
        result = undo_log(load_undoable_log(dst), dst)
        assert result["kept"] == 1
        assert result["undone"] == 0
        assert result["closed"] is True
        assert moved.exists()
        assert moved.read_bytes() != original
        assert not photo.exists()
        saved = load_log(dst)
        assert saved is not None and saved.undone
        assert saved.operations[0].status == "kept"

    def test_kept_op_is_not_retried_when_a_sibling_fails(self, tmp_path, monkeypatch):
        import media_organizer.core.plan as plan_mod

        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        original = src / "a.jpg"
        copy_a = dst / "a.jpg"
        copy_b = dst / "b.jpg"
        original.write_bytes(b"same-bytes")
        copy_a.write_bytes(b"same-bytes")
        copy_b.write_bytes(b"only-left")
        log = new_log(dst)
        log.operations.extend([
            Operation("copy", str(original), str(copy_a), status="done",
                      size=len(b"same-bytes")),
            Operation("copy", str(src / "gone.jpg"), str(copy_b), status="done",
                      size=len(b"only-left")),
        ])
        save_log(log, dst)

        real_discard = plan_mod._discard_copied_file
        monkeypatch.setattr(plan_mod, "_discard_copied_file", lambda path: False)
        first = undo_log(load_undoable_log(dst), dst)
        assert first["kept"] == 1
        assert first["failed"] == 1
        assert first["closed"] is False
        assert copy_a.read_bytes() == b"same-bytes"
        assert copy_b.read_bytes() == b"only-left"
        stuck = load_undoable_log(dst)
        assert stuck is not None and stuck.run_id == log.run_id
        assert [op.status for op in stuck.operations] == ["done", "kept"]

        monkeypatch.setattr(plan_mod, "_discard_copied_file", real_discard)
        second = undo_log(load_undoable_log(dst), dst)
        assert second["kept"] == 0
        assert second["undone"] == 1
        assert second["failed"] == 0
        assert second["closed"] is True
        assert not copy_a.exists()
        assert copy_b.read_bytes() == b"only-left"
        assert load_undoable_log(dst) is None

    def test_kept_legacy_log_is_updated_in_place(self, tmp_path):
        _src, dst, photo, copied = _one_photo_run(tmp_path)
        path = log_path_for(dst)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("run_id", None)
        path.write_text(json.dumps(data), encoding="utf-8")
        photo.unlink()
        log = load_log(dst)
        assert log is not None and log.run_id == ""
        result = undo_log(log, dst)
        assert result["kept"] == 1
        assert result["closed"] is True
        assert copied.exists()
        history = dst / ".media_organizer" / "history"
        assert not history.exists()
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["undone"] is True
        assert saved["operations"][0]["status"] == "kept"
        assert load_undoable_log(dst) is None

    def test_messages_say_kept_files_do_not_block_an_older_run(self):
        lowered = UNDO_LIMITATION.lower()
        assert "only remaining copy" in lowered
        assert "older run" in lowered
        text = undo_result_message({
            "undone": 1,
            "skipped": 0,
            "failed": 0,
            "kept": 2,
            "remaining": 1,
            "closed": True,
        })
        assert "2 left in place" in text
        assert "do not block undoing an older run" in text
        assert "1 other run can still be undone" in text
        still_open = undo_result_message({
            "undone": 0,
            "skipped": 0,
            "failed": 1,
            "kept": 1,
            "remaining": 1,
            "closed": False,
        })
        assert "left in place" in still_open
        assert "do not block" not in still_open
        assert "other run" not in still_open


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


def _path_key(path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


class _ReparseStat:
    """Real stat plus the Windows reparse-point attribute.

    Linux stat results cannot store st_file_attributes, so detection tests
    hand this stand-in to os.lstat for the junction path only.
    """

    def __init__(self, st):
        self._st = st
        self.st_file_attributes = getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def __getattr__(self, name):
        return getattr(self._st, name)


def _patch_lstat_reparse(monkeypatch, junction: Path):
    real_lstat = os.lstat
    junction_key = _path_key(junction)

    def fake_lstat(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        if _path_key(path) == junction_key:
            return _ReparseStat(st)
        return st

    monkeypatch.setattr(os, "lstat", fake_lstat)


def _following_walk(top, topdown=True, onerror=None, followlinks=False):
    """os.walk that follows directory links unless dirnames is edited.

    A budget turns a junction/symlink loop into a test failure instead of
    a hang. followlinks is ignored on purpose: Windows follows junctions
    even when that flag is false.
    """
    budget = {"n": 0}

    def rec(path):
        budget["n"] += 1
        if budget["n"] > 40:
            raise RuntimeError("directory link was followed")
        dirs: list[str] = []
        files: list[str] = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=True)
                    except OSError:
                        is_dir = False
                    if is_dir:
                        dirs.append(entry.name)
                    else:
                        files.append(entry.name)
        except OSError:
            return
        yield path, dirs, files
        for name in dirs:
            yield from rec(os.path.join(path, name))

    yield from rec(os.fspath(top))


class TestWindowsJunctions:
    def test_reparse_attribute_and_symlink_and_isjunction(self, tmp_path, monkeypatch):
        ordinary = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x10)
        reparse = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
        )
        symlink = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
        assert _stat_is_non_descendable_dir(ordinary, "photos") is False
        assert _stat_is_non_descendable_dir(reparse, "Organized") is True
        assert _stat_is_non_descendable_dir(symlink, "alias") is True

        # A Windows stat that carries the attribute field is enough. Do not
        # call isjunction for every normal directory on a large library.
        monkeypatch.setattr(os, "name", "nt")

        def unexpected(path):
            raise AssertionError(f"isjunction was called for {path}")

        monkeypatch.setattr(os.path, "isjunction", unexpected, raising=False)
        assert _stat_is_non_descendable_dir(ordinary, "photos") is False

        bare = SimpleNamespace(st_mode=stat.S_IFDIR)  # no attribute field
        monkeypatch.setattr(
            os.path, "isjunction", lambda path: path == "J", raising=False)
        assert _stat_is_non_descendable_dir(bare, "J") is True
        assert _stat_is_non_descendable_dir(bare, "other") is False

        folder = tmp_path / "folder"
        folder.mkdir()
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=True)
        assert _is_non_descendable_dir(str(folder)) is False
        assert _is_non_descendable_dir(str(link)) is True

    def test_reparse_point_directory_is_not_scanned_or_counted(
            self, tmp_path, monkeypatch):
        root = tmp_path.resolve() / "photos"
        junction = root / "Organized"
        nested = root / "sub"
        root.mkdir()
        junction.mkdir()
        nested.mkdir()
        (root / "a.jpg").write_bytes(b"a")
        (nested / "b.jpg").write_bytes(b"b")
        (junction / "via.jpg").write_bytes(b"v")
        (junction / "deep").mkdir()
        (junction / "deep" / "c.jpg").write_bytes(b"c")
        _patch_lstat_reparse(monkeypatch, junction)

        options = OrganizeOptions(source_dir=root, dest_dir=tmp_path / "out")
        found = list(scan_media_files(options))
        assert sorted(p.name for p in found) == ["a.jpg", "b.jpg"]
        assert count_media_files(options) == len(found)

    def test_directory_symlink_is_not_followed(self, tmp_path, monkeypatch):
        root = tmp_path.resolve() / "photos"
        outside = tmp_path.resolve() / "outside"
        root.mkdir()
        outside.mkdir()
        (root / "a.jpg").write_bytes(b"a")
        (root / "sub").mkdir()
        (root / "sub" / "c.jpg").write_bytes(b"c")
        (outside / "b.jpg").write_bytes(b"b")
        link = root / "loop"
        link.symlink_to(root, target_is_directory=True)
        other = root / "alias"
        other.symlink_to(outside, target_is_directory=True)
        assert link.is_dir() and other.is_dir()

        monkeypatch.setattr(os, "walk", _following_walk)
        options = OrganizeOptions(source_dir=root, dest_dir=tmp_path / "out")
        found = list(scan_media_files(options))
        assert sorted(p.name for p in found) == ["a.jpg", "c.jpg"]
        assert count_media_files(options) == len(found)

    def test_symlink_loop_is_followed_when_detection_is_off(
            self, tmp_path, monkeypatch):
        root = tmp_path.resolve() / "photos"
        root.mkdir()
        (root / "a.jpg").write_bytes(b"a")
        link = root / "loop"
        link.symlink_to(root, target_is_directory=True)
        monkeypatch.setattr(os, "walk", _following_walk)
        monkeypatch.setattr(
            "media_organizer.core.organizer._is_non_descendable_dir",
            lambda path: False,
        )
        with pytest.raises(RuntimeError, match="directory link was followed"):
            list(scan_media_files(
                OrganizeOptions(source_dir=root, dest_dir=tmp_path / "out")))

    def test_entered_junction_is_pruned_by_resolved_path(
            self, tmp_path, monkeypatch):
        """A junction path string is not the resolved destination.

        Windows os.walk can already be inside the junction before the scan
        decides whether to prune. The files in that directory belong to the
        destination and must be skipped, without resolve() on each file.
        """
        root = tmp_path.resolve() / "photos"
        outside = tmp_path.resolve() / "elsewhere"
        junction = root / "Organized"
        root.mkdir()
        outside.mkdir()
        junction.mkdir()
        (root / "a.jpg").write_bytes(b"a")
        (root / "sub").mkdir()
        (root / "sub" / "b.jpg").write_bytes(b"b")
        (junction / "already.jpg").write_bytes(b"already")
        (junction / "nested").mkdir()
        (junction / "nested" / "deep.jpg").write_bytes(b"deep")
        (outside / "marker.txt").write_bytes(b"x")

        resolved: list[Path] = []
        real_resolve = Path.resolve

        def fake_resolve(self, *args, **kwargs):
            resolved.append(Path(self))
            if _path_key(self) == _path_key(junction):
                return real_resolve(outside)
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", fake_resolve)
        _patch_lstat_reparse(monkeypatch, junction)

        def fake_walk(top, topdown=True, onerror=None, followlinks=False):
            junction_dirs = ["nested"]
            yield os.fspath(junction), junction_dirs, ["already.jpg"]
            if "nested" in junction_dirs:
                yield os.fspath(junction / "nested"), [], ["deep.jpg"]
            source_dirs = ["sub", "Organized"]
            yield os.fspath(root), source_dirs, ["a.jpg"]
            if "sub" in source_dirs:
                yield os.fspath(root / "sub"), [], ["b.jpg"]
            if "Organized" in source_dirs:
                yield os.fspath(junction), [], ["already.jpg"]

        monkeypatch.setattr(os, "walk", fake_walk)
        options = OrganizeOptions(source_dir=root, dest_dir=junction)
        found = list(scan_media_files(options))
        assert sorted(p.name for p in found) == ["a.jpg", "b.jpg"]
        assert count_media_files(options) == len(found)
        assert resolved
        assert not any(path.suffix.lower() == ".jpg" for path in resolved)

    def test_symlink_to_a_media_file_is_still_scanned(self, tmp_path):
        root = tmp_path / "photos"
        root.mkdir()
        real = root / "real.jpg"
        real.write_bytes(b"jpeg")
        alias = root / "alias.jpg"
        alias.symlink_to(real)
        options = OrganizeOptions(source_dir=root, dest_dir=tmp_path / "out")
        found = sorted(p.name for p in scan_media_files(options))
        assert found == ["alias.jpg", "real.jpg"]
        assert count_media_files(options) == 2

    @pytest.mark.skipif(
        os.name != "nt",
        reason=(
            "mklink /J creates a directory junction only on Windows. "
            "This Linux run does not create one; detection and pruning "
            "are covered by the reparse-attribute and symlink tests above."
        ),
    )
    def test_junction_destination_inside_source_is_pruned(self, tmp_path):
        import subprocess

        root = tmp_path / "photos"
        target = tmp_path / "elsewhere"
        link = root / "Organized"
        loop = root / "loop"
        root.mkdir()
        target.mkdir()
        (root / "a.jpg").write_bytes(b"jpeg")
        (root / "sub").mkdir()
        (root / "sub" / "b.jpg").write_bytes(b"jpeg")
        (target / "already.jpg").write_bytes(b"jpeg")
        (target / "nested").mkdir()
        (target / "nested" / "deep.jpg").write_bytes(b"jpeg")
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
        )
        if created.returncode != 0 or not link.exists():
            pytest.skip(
                "mklink /J could not create a directory junction: "
                + (created.stderr or created.stdout or "no output").strip()
            )
        looped = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(loop), str(root)],
            capture_output=True, text=True,
        )
        if looped.returncode != 0 or not loop.exists():
            pytest.skip(
                "mklink /J could not create a loop junction: "
                + (looped.stderr or looped.stdout or "no output").strip()
            )
        options = OrganizeOptions(source_dir=root, dest_dir=link)
        # os.walk has no visited set. A junction back into the source is a
        # different path each time, so a missed filter grows the stack
        # without end. Stop on the second visit to the same directory inode.
        real_walk = os.walk

        def guarded_walk(top, topdown=True, onerror=None, followlinks=False):
            seen: dict[tuple, int] = {}
            for dirpath, dirnames, filenames in real_walk(
                    top, topdown=topdown, onerror=onerror,
                    followlinks=followlinks):
                try:
                    st = os.stat(dirpath)
                    key = (st.st_dev, st.st_ino)
                except OSError:
                    key = (dirpath,)
                seen[key] = seen.get(key, 0) + 1
                if seen[key] > 1:
                    raise RuntimeError(
                        "walk revisited a directory; junction loop was followed"
                    )
                yield dirpath, dirnames, filenames

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(os, "walk", guarded_walk)
        try:
            found = list(scan_media_files(options))
            assert sorted(p.name for p in found) == ["a.jpg", "b.jpg"]
            assert count_media_files(options) == len(found)
        finally:
            monkeypatch.undo()


class TestDryRunUndoDoesNotTouchFiles:
    def test_copy_undo_does_not_delete_a_matching_destination(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        photo = make_jpeg_with_exif(
            src / "IMG_1.jpg", datetime(2024, 7, 15, 10, 30, 0))
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        plan = build_plan(options)
        real_log, _summary = execute_plan(plan, options)
        copied = plan[0].destination
        payload = copied.read_bytes()
        saved = log_path_for(dst).read_bytes()
        history = dst / ".media_organizer" / "history"
        assert not history.exists()
        assert real_log.run_id

        dry = OrganizeOptions(source_dir=src, dest_dir=dst, dry_run=True)
        log, summary = execute_plan(plan, dry)
        assert summary["dry_run"] is True
        assert summary["copied"] == 1
        op = log.operations[0]
        assert op.status == "done"
        assert op.action == "copy"
        assert op.size == -1
        assert op.sha256 == ""
        assert op.destination == str(copied)
        assert load_log(dst).run_id == real_log.run_id

        result = undo_log(log, dst)

        assert result["undone"] == 0
        assert result["failed"] == 0
        assert result["closed"] is False
        assert copied.is_file()
        assert copied.read_bytes() == payload
        assert photo.read_bytes() == payload
        assert log.undone is False
        assert log.operations[0].status == "done"
        assert log_path_for(dst).read_bytes() == saved
        assert not history.exists()
        assert list_run_logs(dst)[0].run_id == real_log.run_id
        assert list_run_logs(dst)[0].undone is False

        real = undo_log(load_log(dst), dst)
        assert real["undone"] == 1
        assert not copied.exists()
        assert photo.is_file()

    def test_move_undo_does_not_relocate_a_matching_destination(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        photo = make_jpeg_with_exif(
            src / "IMG_1.jpg", datetime(2024, 7, 15, 10, 30, 0))
        options = OrganizeOptions(
            source_dir=src, dest_dir=dst, copy_mode=False)
        plan = build_plan(options)
        dest = plan[0].destination
        dest.parent.mkdir(parents=True)
        payload = photo.read_bytes()
        dest.write_bytes(payload)

        dry = OrganizeOptions(
            source_dir=src, dest_dir=dst, copy_mode=False, dry_run=True)
        log, summary = execute_plan(plan, dry)
        assert summary["dry_run"] is True
        assert summary["moved"] == 1
        op = log.operations[0]
        assert op.action == "move"
        assert op.status == "done"
        assert op.size == -1
        assert op.sha256 == ""
        assert op.destination == str(dest)

        result = undo_log(log, dst)

        assert result["undone"] == 0
        assert result["closed"] is False
        assert dest.is_file()
        assert dest.read_bytes() == payload
        assert photo.is_file()
        assert photo.read_bytes() == payload
        assert not any(src.rglob("*restored*"))
        assert not any(dst.rglob("*restored*"))
        assert load_log(dst) is None
        assert not log_path_for(dst).exists()

    def test_undo_does_not_write_a_log_or_drop_an_open_journal(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        photo = make_jpeg_with_exif(
            src / "IMG_1.jpg", datetime(2024, 7, 15, 10, 30, 0))
        options = OrganizeOptions(source_dir=src, dest_dir=dst, dry_run=True)
        plan = build_plan(options)
        dest = plan[0].destination
        dest.parent.mkdir(parents=True)
        payload = photo.read_bytes()
        dest.write_bytes(payload)
        with JournalWriter(dst) as writer:
            writer.record("copy", str(photo), str(dest))
        assert find_unfinished_journal(dst) is not None

        rescanned = build_plan(options)
        assert rescanned[0].destination == dest
        log, summary = execute_plan(rescanned, options)
        assert summary["dry_run"] is True
        assert log.operations[0].destination == str(dest)
        assert log.operations[0].size == -1

        result = undo_log(log, dst)

        assert result["undone"] == 0
        assert result["closed"] is False
        assert dest.read_bytes() == payload
        assert photo.read_bytes() == payload
        assert find_unfinished_journal(dst) is not None
        assert load_log(dst) is None
        assert not log_path_for(dst).exists()
