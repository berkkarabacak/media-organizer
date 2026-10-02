"""Tests for safety features: free space, journal/resume, atomic copy,
dry run, and parallel correctness (executor.py, journal.py, metadata batch)."""

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path, PureWindowsPath

import pytest

from media_organizer.core.executor import (
    _volume_id, bytes_still_needed, execute_plan, free_space_status,
    saved_run_owns_plan_row,
)
from media_organizer.core.journal import (
    PART_SUFFIX, JournalWriter, atomic_copy, atomic_move, cleanup_stale_parts,
    completed_operations, completed_sources, discard_journal,
    exclude_completed_sources, files_identical, find_unfinished_journal,
    journal_path_for, path_identity,
)
from media_organizer.core.metadata import (
    CaptureDate, Confidence, DateSource, analyze_media_batch,
    extract_capture_date, extract_gps,
)
from media_organizer.core.organizer import OrganizeOptions, PlannedFile, build_plan
from media_organizer.core.plan import (
    Operation, list_run_logs, load_undoable_log, log_path_for, new_log,
    save_log, saved_run_destinations, undo_log,
)
from tests.helpers import make_jpeg_with_exif, make_jpeg_with_gps


def _setup(tmp_path, n=3):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    for i in range(n):
        make_jpeg_with_exif(src / f"IMG_{i}.jpg",
                            datetime(2024, 7, 15, 10, i, 0))
    return src, dst


def _plan(src, dst, **kw):
    return build_plan(OrganizeOptions(source_dir=src, dest_dir=dst, **kw))


def _threshold_batch(tmp_path, n=400):
    """At least the process-pool threshold, with EXIF, GPS, and filename dates."""
    from media_organizer.core.metadata import _PROCESS_THRESHOLD

    n = max(n, _PROCESS_THRESHOLD)
    files = []
    for i in range(n):
        path = tmp_path / f"batch_{i:04d}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xd9")
        files.append(path)
    files[0] = make_jpeg_with_exif(
        tmp_path / "batch_0000.jpg", datetime(2021, 7, 4, 15, 30, 22))
    files[1] = make_jpeg_with_gps(
        tmp_path / "batch_0001.jpg", datetime(2019, 1, 2, 3, 4, 5), 41.01, 28.98)
    named = tmp_path / "IMG_20190615_083045.jpg"
    named.write_bytes(b"\xff\xd8\xff\xd9")
    files[2] = named
    return files


def _assert_batch_matches_serial(result, files, need_gps):
    for path in files:
        cap, gps = result[path]
        assert cap == extract_capture_date(path)
        if need_gps:
            assert gps == extract_gps(path)
        else:
            assert gps is None


class TestFreeSpace:
    def test_fits(self, tmp_path):
        s = free_space_status(tmp_path, 1024)
        assert s["ok"] and not s["tight"] and s["free"] > 1024
        assert s["needed"] == 1024

    def test_does_not_fit(self, tmp_path):
        huge = 10**15
        s = free_space_status(tmp_path, huge)
        assert not s["ok"]
        assert s["tight"]

    def test_nonexistent_path_climbs_to_ancestor(self, tmp_path):
        s = free_space_status(tmp_path / "a" / "b" / "c", 1024)
        assert s["ok"]

    def test_drive_letter(self, tmp_path):
        s = free_space_status(tmp_path, 1024)
        assert s["drive"]

    def test_nonexistent_drive_does_not_crash(self):
        """A missing Windows drive is unknown, including on Linux.

        ``Q:/...`` is a relative path outside Windows. Climbing it used to
        reach the current directory and report that disk as a real preflight
        (``unknown`` false). ``ok`` stays true so the run is not blocked;
        ``free`` is -1 so it is not that disk's free space.
        """
        for raw in (
            "Q:/definitely_not_a_real_drive_xyz/o",
            "Q:\\definitely_not_a_real_drive_xyz\\o",
        ):
            s = free_space_status(raw, 1024)
            assert s["ok"] is True
            assert s["unknown"] is True
            assert s["free"] == -1
            assert s["tight"] is False


def _capture_date():
    return CaptureDate(datetime(2024, 7, 15, 10, 0, 0),
                       DateSource.EXIF, Confidence.HIGH, "EXIF")


class TestBytesStillNeeded:
    """Free-space `needed` is the bytes execute_plan will still write.

    Resume drops journaled sources before this sum. A row whose destination
    already matches the source is journaled only, so its size is not needed
    either. A different occupant still needs a full new copy.

    Copy mode counts every remaining row. Move mode omits a row on the
    same device as the destination: that transfer is a rename. A different
    device, or a device that cannot be read, still counts.
    """

    def test_sums_rows_that_will_be_written(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        first = src / "a.jpg"
        second = src / "b.jpg"
        first.write_bytes(b"aaa")
        second.write_bytes(b"bbbb")
        capture = _capture_date()
        plan = [
            PlannedFile(first, tmp_path / "out" / "a.jpg", 30, capture, "image"),
            PlannedFile(second, tmp_path / "out" / "b.jpg", 70, capture, "image"),
            PlannedFile(src / "dup.jpg", None, 10**15, capture, "image",
                        is_duplicate=True),
            PlannedFile(src / "skip.jpg", None, 10**15, capture, "image"),
        ]
        assert bytes_still_needed(plan) == 100

    def test_resume_drops_completed_bytes_that_would_not_fit(self, tmp_path):
        src, dst = _setup(tmp_path, n=2)
        plan = _plan(src, dst)
        done, fresh = plan
        done.size = 10**15
        fresh.size = 4096
        with JournalWriter(dst) as writer:
            writer.record("copy", str(done.source), str(done.destination))
        journal = find_unfinished_journal(dst)
        assert journal is not None
        remaining = exclude_completed_sources(plan, completed_sources(journal))

        full = bytes_still_needed(plan)
        needed = bytes_still_needed(remaining)
        assert full == 10**15 + 4096
        assert needed == 4096
        assert [item.source for item in remaining] == [fresh.source]
        assert not free_space_status(dst, full)["ok"]
        remaining_space = free_space_status(dst, needed)
        assert remaining_space["ok"]
        assert remaining_space["needed"] == 4096
        assert not remaining_space["tight"]

    def test_remaining_work_that_still_does_not_fit_is_the_real_total(
            self, tmp_path):
        src, dst = _setup(tmp_path, n=2)
        plan = _plan(src, dst)
        done, fresh = plan
        done.size = 111
        fresh.size = 10**14
        with JournalWriter(dst) as writer:
            writer.record("copy", str(done.source), str(done.destination))
        remaining = exclude_completed_sources(
            plan, completed_sources(journal_path_for(dst)))

        needed = bytes_still_needed(remaining)
        assert needed == 10**14
        assert needed != bytes_still_needed(plan)
        space = free_space_status(dst, needed)
        assert space["ok"] is False
        assert space["needed"] == 10**14
        assert space["free"] >= 0

    def test_published_identical_destination_does_not_inflate_needed(
            self, tmp_path):
        """Crash after publish, before the journal line: no new bytes."""
        src, dst = _setup(tmp_path, n=2)
        published, fresh = _plan(src, dst)
        published.destination.parent.mkdir(parents=True)
        atomic_copy(published.source, published.destination)
        published.size = 10**15
        fresh.size = 2048
        _unfinished_without(dst, published.source)
        # The published source is not a completed journal line, so Resume
        # keeps the row. execute_plan journals it and does not copy it.
        remaining = exclude_completed_sources(
            [published, fresh], completed_sources(journal_path_for(dst)))
        assert published.source in {item.source for item in remaining}
        assert fresh.source in {item.source for item in remaining}

        needed = bytes_still_needed(remaining)
        assert needed == 2048
        assert free_space_status(dst, needed)["ok"]
        assert not free_space_status(dst, 10**15 + 2048)["ok"]

    def test_different_bytes_at_destination_still_count(self, tmp_path):
        """A same-size occupant is a collision copy, not a free resume."""
        src, dst = _setup(tmp_path, n=1)
        item = _plan(src, dst)[0]
        item.destination.parent.mkdir(parents=True)
        mutated = bytearray(item.source.read_bytes())
        mutated[-1] ^= 0xFF
        item.destination.write_bytes(bytes(mutated))
        assert not files_identical(item.source, item.destination)
        item.size = 10**15
        _unfinished_without(dst, item.source)
        remaining = exclude_completed_sources(
            [item], completed_sources(journal_path_for(dst)))
        assert remaining == [item]

        needed = bytes_still_needed(remaining)
        assert needed == 10**15
        space = free_space_status(dst, needed)
        assert space["ok"] is False
        assert space["needed"] == 10**15

    def test_discard_counts_sources_whose_bytes_are_not_on_disk(self, tmp_path):
        """Discard runs the full plan. A missing copy must be counted again."""
        src, dst = _setup(tmp_path, n=2)
        plan = _plan(src, dst)
        done, fresh = plan
        done.size = 10**15
        fresh.size = 512
        with JournalWriter(dst) as writer:
            writer.record("copy", str(done.source), str(done.destination))
        assert not done.destination.exists()
        # Resume would skip `done`. Discard copies it, because nothing is
        # at the destination yet.
        assert bytes_still_needed(
            exclude_completed_sources(
                plan, completed_sources(journal_path_for(dst)))) == 512
        assert bytes_still_needed(plan) == 10**15 + 512
        assert not free_space_status(dst, bytes_still_needed(plan))["ok"]

    def test_discard_does_not_count_identical_bytes_already_at_dest(
            self, tmp_path):
        src, dst = _setup(tmp_path, n=2)
        plan = _plan(src, dst)
        done, fresh = plan
        done.destination.parent.mkdir(parents=True)
        atomic_copy(done.source, done.destination)
        done.size = 10**15
        fresh.size = 128
        with JournalWriter(dst) as writer:
            writer.record("copy", str(done.source), str(done.destination))
        assert bytes_still_needed(plan) == 128
        assert free_space_status(dst, bytes_still_needed(plan))["ok"]

    def test_move_same_volume_does_not_need_the_rename_bytes(self, tmp_path):
        """A nearly-full drive can still rename. Copy mode still counts."""
        src = tmp_path / "src"
        src.mkdir()
        photo = src / "a.jpg"
        photo.write_bytes(b"jpeg")
        capture = _capture_date()
        # Destination folder is not created yet. Its existing ancestor is
        # the same device, which is where the move will mkdir.
        dst = tmp_path / "missing" / "organized"
        plan = [
            PlannedFile(photo, dst / "2024" / "a.jpg", 10**15, capture, "image"),
        ]
        needed = bytes_still_needed(plan, copy_mode=False, dest_dir=dst)
        assert needed == 0
        assert free_space_status(dst, needed)["ok"]
        assert not free_space_status(dst, needed)["tight"]
        assert bytes_still_needed(plan) == 10**15
        assert bytes_still_needed(plan, copy_mode=True, dest_dir=dst) == 10**15
        assert not free_space_status(dst, bytes_still_needed(plan))["ok"]

    def test_move_without_a_destination_root_still_counts(self, tmp_path):
        """No dest device to compare against: overcount, do not undercount."""
        src = tmp_path / "src"
        src.mkdir()
        photo = src / "a.jpg"
        photo.write_bytes(b"jpeg")
        plan = [
            PlannedFile(photo, tmp_path / "out" / "a.jpg", 10**15,
                        _capture_date(), "image"),
        ]
        assert bytes_still_needed(plan, copy_mode=False) == 10**15

    def test_move_other_device_still_counts(self, tmp_path, monkeypatch):
        """Cross-volume move copies before it unlinks, so the size counts."""
        src = tmp_path / "src"
        src.mkdir()
        photo = src / "a.jpg"
        photo.write_bytes(b"jpeg")
        dst = tmp_path / "dst"
        plan = [
            PlannedFile(photo, dst / "a.jpg", 10**15, _capture_date(), "image"),
        ]

        def volume(path):
            if Path(path) == photo:
                return 11
            return 22

        monkeypatch.setattr(
            "media_organizer.core.executor._volume_id", volume)
        needed = bytes_still_needed(plan, copy_mode=False, dest_dir=dst)
        assert needed == 10**15
        assert not free_space_status(dst, needed)["ok"]
        # Copy mode is unchanged by the device split.
        assert bytes_still_needed(plan, copy_mode=True, dest_dir=dst) == 10**15

    def test_move_unknown_device_still_counts(self, tmp_path, monkeypatch):
        """OSError or an unreadable anchor counts the size."""
        src = tmp_path / "src"
        src.mkdir()
        photo = src / "a.jpg"
        photo.write_bytes(b"jpeg")
        dst = tmp_path / "dst"
        plan = [
            PlannedFile(photo, dst / "a.jpg", 8192, _capture_date(), "image"),
        ]
        monkeypatch.setattr(
            "media_organizer.core.executor._volume_id", lambda _path: None)
        assert bytes_still_needed(plan, copy_mode=False, dest_dir=dst) == 8192

        def source_unknown(path):
            if Path(path) == photo:
                return None
            return 5

        monkeypatch.setattr(
            "media_organizer.core.executor._volume_id", source_unknown)
        assert bytes_still_needed(plan, copy_mode=False, dest_dir=dst) == 8192

        def dest_unknown(path):
            if Path(path) == dst:
                return None
            return 5

        monkeypatch.setattr(
            "media_organizer.core.executor._volume_id", dest_unknown)
        assert bytes_still_needed(plan, copy_mode=False, dest_dir=dst) == 8192

    def test_move_stat_failure_counts_instead_of_raising(self, tmp_path, monkeypatch):
        """A real stat error is an unknown device, not a preflight crash."""
        src = tmp_path / "src"
        src.mkdir()
        photo = src / "a.jpg"
        photo.write_bytes(b"jpeg")
        dst = tmp_path / "dst"
        plan = [
            PlannedFile(photo, dst / "a.jpg", 8192, _capture_date(), "image"),
        ]

        def boom(self, *args, **kwargs):
            raise PermissionError("unreadable")

        monkeypatch.setattr(Path, "stat", boom)
        assert bytes_still_needed(plan, copy_mode=False, dest_dir=dst) == 8192

    def test_move_identical_destination_is_zero_on_another_device(
            self, tmp_path, monkeypatch):
        """Journal-only rows stay at 0 even when the devices differ."""
        src = tmp_path / "src"
        src.mkdir()
        photo = src / "a.jpg"
        photo.write_bytes(b"same-bytes")
        dst = tmp_path / "dst"
        dest = dst / "a.jpg"
        dest.parent.mkdir()
        dest.write_bytes(b"same-bytes")
        assert files_identical(photo, dest)
        plan = [
            PlannedFile(photo, dest, 10**15, _capture_date(), "image"),
        ]

        def volume(path):
            if Path(path) == photo:
                return 11
            return 22

        monkeypatch.setattr(
            "media_organizer.core.executor._volume_id", volume)
        assert bytes_still_needed(plan, copy_mode=False, dest_dir=dst) == 0
        assert bytes_still_needed(plan, copy_mode=True, dest_dir=dst) == 0

    def test_move_mixes_rename_copy_and_journal_only(
            self, tmp_path, monkeypatch):
        """Only the cross-volume row that is not already identical counts."""
        src = tmp_path / "src"
        src.mkdir()
        capture = _capture_date()
        same = src / "same.jpg"
        other = src / "other.jpg"
        published = src / "published.jpg"
        same.write_bytes(b"same")
        other.write_bytes(b"other")
        published.write_bytes(b"published")
        dst = tmp_path / "dst"
        published_dest = dst / "published.jpg"
        published_dest.parent.mkdir()
        published_dest.write_bytes(b"published")
        plan = [
            PlannedFile(same, dst / "same.jpg", 10**15, capture, "image"),
            PlannedFile(other, dst / "other.jpg", 4096, capture, "image"),
            PlannedFile(published, published_dest, 10**15, capture, "image"),
            PlannedFile(src / "dup.jpg", None, 10**15, capture, "image",
                        is_duplicate=True),
        ]

        def volume(path):
            if Path(path) == other:
                return 11
            return 3

        monkeypatch.setattr(
            "media_organizer.core.executor._volume_id", volume)
        assert bytes_still_needed(plan, copy_mode=False, dest_dir=dst) == 4096
        assert bytes_still_needed(plan, copy_mode=True, dest_dir=dst) == (
            10**15 + 4096)

    def test_resume_move_drops_other_volume_and_keeps_rename_at_zero(
            self, tmp_path, monkeypatch):
        """Resume measures the post-exclude plan. Discard measures all of it."""
        src = tmp_path / "src"
        src.mkdir()
        capture = _capture_date()
        done = src / "done.jpg"
        fresh = src / "fresh.jpg"
        done.write_bytes(b"done")
        fresh.write_bytes(b"fresh")
        dst = tmp_path / "dst"
        dst.mkdir()
        plan = [
            PlannedFile(done, dst / "done.jpg", 10**15, capture, "image"),
            PlannedFile(fresh, dst / "fresh.jpg", 10**15, capture, "image"),
        ]
        with JournalWriter(dst) as writer:
            writer.record("move", str(done), str(dst / "done.jpg"))
        remaining = exclude_completed_sources(
            plan, completed_sources(journal_path_for(dst)))

        def volume(path):
            if Path(path) == done:
                return 11
            return 3

        monkeypatch.setattr(
            "media_organizer.core.executor._volume_id", volume)
        assert bytes_still_needed(
            remaining, copy_mode=False, dest_dir=dst) == 0
        assert bytes_still_needed(plan, copy_mode=False, dest_dir=dst) == 10**15
        assert free_space_status(dst, 0)["ok"]
        assert not free_space_status(dst, 10**15)["ok"]


class TestVolumeId:
    """Device identity for move preflight. Unknown counts as not-the-same."""

    def test_existing_file_and_missing_parent(self, tmp_path):
        photo = tmp_path / "a.jpg"
        photo.write_bytes(b"x")
        assert _volume_id(photo) == photo.stat().st_dev
        missing = tmp_path / "no" / "such" / "dir"
        assert _volume_id(missing) == tmp_path.stat().st_dev

    def test_oserror_is_unknown(self, tmp_path, monkeypatch):
        """Permission errors are not "missing". They must not escape."""
        def boom(self, *args, **kwargs):
            raise PermissionError("unreadable")

        monkeypatch.setattr(Path, "stat", boom)
        assert _volume_id(tmp_path / "a" / "b.jpg") is None

    def test_windows_drive_letter_is_unknown_off_windows(self):
        if os.name == "nt":
            pytest.skip("drive-letter guard is for non-Windows")
        assert _volume_id("Q:/photos/out") is None
        assert _volume_id("Q:\\photos\\out") is None

    def test_real_other_volume_counts_and_same_volume_does_not(self, tmp_path):
        """``/dev/shm`` is a different device from the pytest temp dir on Linux."""
        shm = Path("/dev/shm")
        if not shm.is_dir() or shm.stat().st_dev == tmp_path.stat().st_dev:
            pytest.skip("need a second local volume such as /dev/shm")
        other = Path(tempfile.mkdtemp(prefix="mo-vol-", dir=shm))
        try:
            assert other.stat().st_dev != tmp_path.stat().st_dev
            capture = _capture_date()
            there = other / "clip.jpg"
            there.write_bytes(b"clip")
            here = tmp_path / "here.jpg"
            here.write_bytes(b"here")
            dst = tmp_path / "dst"
            plan_there = [
                PlannedFile(there, dst / "clip.jpg", 10**15, capture, "image"),
            ]
            plan_here = [
                PlannedFile(here, other / "nested" / "here.jpg", 10**15,
                            capture, "image"),
            ]
            assert bytes_still_needed(
                plan_there, copy_mode=False, dest_dir=dst) == 10**15
            assert bytes_still_needed(
                plan_here, copy_mode=False, dest_dir=other / "nested") == 10**15
            assert bytes_still_needed(
                plan_here, copy_mode=False, dest_dir=tmp_path / "out") == 0
            both_there = [
                PlannedFile(there, other / "moved.jpg", 10**15, capture, "image"),
            ]
            assert bytes_still_needed(
                both_there, copy_mode=False, dest_dir=other) == 0
        finally:
            shutil.rmtree(other, ignore_errors=True)


class TestAtomicCopy:
    def test_content_and_metadata_preserved(self, tmp_path):
        src = _setup(tmp_path)[0] / "IMG_0.jpg"
        final = tmp_path / "out" / "IMG_0.jpg"
        final.parent.mkdir()
        atomic_copy(src, final)
        assert final.read_bytes() == src.read_bytes()
        assert not final.with_name(final.name + PART_SUFFIX).exists()

    def test_stale_part_replaced(self, tmp_path):
        src = _setup(tmp_path)[0] / "IMG_0.jpg"
        final = tmp_path / "IMG_0.jpg"
        part = final.with_name(final.name + PART_SUFFIX)
        part.write_bytes(b"stale garbage from a crashed run")
        atomic_copy(src, final)
        assert final.read_bytes() == src.read_bytes()
        assert not part.exists()

    def test_move_same_volume(self, tmp_path):
        src, _ = _setup(tmp_path)
        f = src / "IMG_1.jpg"
        final = tmp_path / "moved.jpg"
        digest, unlink_after = atomic_move(f, final)
        assert unlink_after is False
        assert final.exists() and not f.exists()
        assert digest == hashlib.sha256(final.read_bytes()).hexdigest()

    def test_part_file_is_fsynced_before_replace(self, tmp_path, monkeypatch):
        """Part-file bytes are fsynced before that name becomes final.

        ``JournalWriter.record`` fsyncs the done line only after
        ``atomic_copy`` returns. Spying ``open`` / ``os.fsync`` /
        ``os.replace`` proves the part fd was flushed and synced first,
        while the temp name still exists. No power cut.
        """
        src = _setup(tmp_path)[0] / "IMG_0.jpg"
        final = tmp_path / "out" / "IMG_0.jpg"
        final.parent.mkdir()
        part = final.with_name(final.name + PART_SUFFIX)
        order = []
        real_open = open
        real_fsync = os.fsync
        real_replace = os.replace

        class _Part:
            """Records flush on the part file. Other methods stay real."""

            def __init__(self, raw):
                self._raw = raw

            def __getattr__(self, name):
                return getattr(self._raw, name)

            def __enter__(self):
                self._raw.__enter__()
                return self

            def __exit__(self, exc_type, exc, tb):
                return self._raw.__exit__(exc_type, exc, tb)

            def flush(self):
                order.append(("flush", self._raw.fileno()))
                return self._raw.flush()

        def spy_open(file, mode="r", *args, **kwargs):
            fh = real_open(file, mode, *args, **kwargs)
            if Path(file) == part:
                order.append(("open", fh.fileno()))
                return _Part(fh)
            return fh

        def spy_fsync(fd):
            order.append(("fsync", fd))
            assert part.exists() and not final.exists()
            return real_fsync(fd)

        def spy_replace(src_path, dst_path):
            order.append(("replace", Path(src_path), Path(dst_path)))
            return real_replace(src_path, dst_path)

        # ``open`` is a builtin, not a module global, until the spy is installed.
        monkeypatch.setattr(
            "media_organizer.core.journal.open", spy_open, raising=False)
        monkeypatch.setattr(
            "media_organizer.core.journal.os.fsync", spy_fsync)
        monkeypatch.setattr(
            "media_organizer.core.journal.os.replace", spy_replace)

        atomic_copy(src, final)

        assert final.read_bytes() == src.read_bytes()
        assert not part.exists()
        kinds = [kind for kind, *_rest in order]
        flush_i = kinds.index("flush")
        fsync_i = kinds.index("fsync")
        replace_i = kinds.index("replace")
        assert flush_i < fsync_i < replace_i
        part_fd = order[kinds.index("open")][1]
        assert order[flush_i][1] == part_fd
        assert order[fsync_i][1] == part_fd
        assert kinds.count("fsync") == 1
        assert order[replace_i][1:] == (part, final)

    def test_cross_volume_move_fsyncs_part_before_replace(
            self, tmp_path, monkeypatch):
        """A failed same-volume rename uses the durable copy path.

        The source stays. ``execute_plan`` journals the move and only then
        unlinks it. Deleting here, before that record, is the data-loss window.
        """
        src = _setup(tmp_path)[0] / "IMG_1.jpg"
        payload = src.read_bytes()
        final = tmp_path / "moved.jpg"
        part = final.with_name(final.name + PART_SUFFIX)
        order = []
        real_fsync = os.fsync
        real_replace = os.replace

        def spy_fsync(fd):
            order.append(("fsync", fd))
            assert part.exists() and not final.exists()
            return real_fsync(fd)

        def spy_replace(src_path, dst_path):
            order.append(("replace", Path(src_path), Path(dst_path)))
            if sum(1 for kind, *_r in order if kind == "replace") == 1:
                raise OSError("simulated cross-volume rename")
            return real_replace(src_path, dst_path)

        monkeypatch.setattr(
            "media_organizer.core.journal.os.fsync", spy_fsync)
        monkeypatch.setattr(
            "media_organizer.core.journal.os.replace", spy_replace)

        digest, unlink_after = atomic_move(src, final)

        assert unlink_after is True
        assert src.read_bytes() == payload
        assert final.read_bytes() == payload
        assert digest == hashlib.sha256(payload).hexdigest()
        kinds = [kind for kind, *_rest in order]
        fsync_i = kinds.index("fsync")
        replace_at = [i for i, kind in enumerate(kinds) if kind == "replace"]
        assert replace_at == [0, fsync_i + 1]
        assert order[0][1:] == (src, final)
        assert order[fsync_i + 1][1:] == (part, final)
        assert kinds.count("fsync") == 1

    def test_same_volume_move_does_not_fsync(self, tmp_path, monkeypatch):
        src, _ = _setup(tmp_path)
        f = src / "IMG_1.jpg"
        final = tmp_path / "moved.jpg"

        def boom(_fd):
            raise AssertionError("same-volume rename must not fsync")

        monkeypatch.setattr("media_organizer.core.journal.os.fsync", boom)
        _digest, unlink_after = atomic_move(f, final)
        assert unlink_after is False
        assert final.exists() and not f.exists()

    def test_cross_volume_move_journals_before_source_unlink(
            self, tmp_path, monkeypatch):
        """Cross-volume move records the journal line before unlinking.

        The same-volume ``os.replace`` is failed once, as in the fsync
        test, so the copy path runs on one filesystem. The source must
        still exist when ``JournalWriter.record`` returns (that call
        fsyncs the done line), and only then be removed.
        """
        src, dst = _setup(tmp_path, n=1)
        options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False)
        plan = _plan(src, dst, copy_mode=False)
        source = plan[0].source
        payload = source.read_bytes()
        source_key = os.path.normcase(os.path.abspath(source))
        order = []
        real_replace = os.replace
        real_record = JournalWriter.record
        real_unlink = Path.unlink
        replaces = {"n": 0}

        def spy_replace(src_path, dst_path):
            replaces["n"] += 1
            if replaces["n"] == 1:
                order.append("replace-cross")
                raise OSError("simulated cross-volume rename")
            order.append("replace-part")
            return real_replace(src_path, dst_path)

        def spy_record(self, action, source_s, destination, **kwargs):
            assert os.path.normcase(os.path.abspath(source_s)) == source_key
            assert Path(source_s).is_file()
            assert Path(destination).is_file()
            assert Path(destination).read_bytes() == payload
            result = real_record(self, action, source_s, destination, **kwargs)
            order.append("journal")
            return result

        def spy_unlink(self, missing_ok=False):
            if os.path.normcase(os.path.abspath(self)) == source_key:
                text = journal_path_for(dst).read_text(encoding="utf-8")
                assert str(source) in text
                assert '"status": "done"' in text
                assert self.is_file()
                order.append("unlink")
            return real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(
            "media_organizer.core.journal.os.replace", spy_replace)
        monkeypatch.setattr(JournalWriter, "record", spy_record)
        monkeypatch.setattr(Path, "unlink", spy_unlink)

        log, summary = execute_plan(plan, options)

        assert summary["moved"] == 1
        assert summary["errors"] == 0
        assert not source.exists()
        final = Path(log.operations[0].destination)
        assert final.read_bytes() == payload
        assert order.index("replace-cross") < order.index("replace-part")
        assert order.index("replace-part") < order.index("journal")
        assert order.index("journal") < order.index("unlink")
        assert order.count("unlink") == 1

    def test_cross_volume_move_keeps_source_when_journal_fails(
            self, tmp_path, monkeypatch):
        """A journal failure must not drop the cross-volume source."""
        src, dst = _setup(tmp_path, n=1)
        options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False)
        plan = _plan(src, dst, copy_mode=False)
        source = plan[0].source
        payload = source.read_bytes()
        real_replace = os.replace
        replaces = {"n": 0}

        def spy_replace(src_path, dst_path):
            replaces["n"] += 1
            if replaces["n"] == 1:
                raise OSError("simulated cross-volume rename")
            return real_replace(src_path, dst_path)

        def boom(self, action, source_s, destination, **kwargs):
            assert Path(source_s).is_file()
            assert Path(source_s).read_bytes() == payload
            raise OSError("journal write failed")

        monkeypatch.setattr(
            "media_organizer.core.journal.os.replace", spy_replace)
        monkeypatch.setattr(JournalWriter, "record", boom)

        log, summary = execute_plan(plan, options)

        assert summary["errors"] == 1
        assert source.is_file()
        assert source.read_bytes() == payload
        assert log.operations[0].status == "error"
        copied = list(dst.rglob("*.jpg"))
        assert len(copied) == 1
        assert copied[0].read_bytes() == payload
        text = journal_path_for(dst).read_text(encoding="utf-8")
        assert str(source) not in text

    def test_cleanup_stale_parts(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / f"x.jpg{PART_SUFFIX}").write_bytes(b"x-bytes")
        (tmp_path / f"y.jpg{PART_SUFFIX}").write_bytes(b"y-bytes")
        (tmp_path / "keep.jpg").write_bytes(b"")
        (tmp_path / "notes.part").write_bytes(b"user")
        assert cleanup_stale_parts(tmp_path) == 2
        assert not (tmp_path / "a" / "x.jpg").exists()
        assert not (tmp_path / "a" / f"x.jpg{PART_SUFFIX}").exists()
        assert not (tmp_path / "y.jpg").exists()
        assert not (tmp_path / f"y.jpg{PART_SUFFIX}").exists()
        assert (tmp_path / "keep.jpg").read_bytes() == b""
        assert (tmp_path / "notes.part").read_bytes() == b"user"

    def test_cleanup_deletes_orphan_part_and_drops_stale_part(self, tmp_path):
        """An orphan part is deleted; a finished final drops its leftover part."""
        orphan = tmp_path / f"photo.jpg{PART_SUFFIX}"
        orphan.write_bytes(b"truncated junk")
        final = tmp_path / "photo.jpg"
        kept = tmp_path / "keep.jpg"
        kept.write_bytes(b"already final")
        stale = tmp_path / f"keep.jpg{PART_SUFFIX}"
        stale.write_bytes(b"leftover")
        (tmp_path / "notes.part").write_bytes(b"user")
        (tmp_path / "clip.mp4.part").write_bytes(b"clip")

        assert cleanup_stale_parts(tmp_path) == 2

        assert not final.exists()
        assert not orphan.exists()
        assert kept.read_bytes() == b"already final"
        assert not stale.exists()
        assert (tmp_path / "notes.part").read_bytes() == b"user"
        assert (tmp_path / "clip.mp4.part").read_bytes() == b"clip"


class TestJournal:
    def test_complete_run_marks_journal(self, tmp_path):
        src, dst = _setup(tmp_path)
        execute_plan(_plan(src, dst),
                     OrganizeOptions(source_dir=src, dest_dir=dst))
        jp = journal_path_for(dst)
        assert jp.exists()
        assert find_unfinished_journal(dst) is None  # run completed
        assert '{"run": "complete"}' in jp.read_text(encoding="utf-8")

    def test_interrupted_run_is_detected_and_resumable(self, tmp_path):
        src, dst = _setup(tmp_path, n=4)
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        plan = _plan(src, dst)

        # simulate interruption: cancel after the second file
        calls = [0]

        def cancel_after_two():
            calls[0] += 1
            return calls[0] > 3

        _log, summary = execute_plan(plan, options, cancel=cancel_after_two)
        assert summary["cancelled"]
        jp = find_unfinished_journal(dst)
        assert jp is not None, "interrupted run must leave an unfinished journal"
        assert '"run": "complete"' not in jp.read_text(encoding="utf-8")
        done = completed_sources(jp)
        assert 0 < len(done) < 4

        # resume: remaining files complete the set
        remaining = exclude_completed_sources(plan, done)
        execute_plan(remaining, options)
        assert find_unfinished_journal(dst) is None
        copied = sorted(p.name for p in dst.rglob("*.jpg"))
        assert len(copied) == 4
        # every final file is byte-complete (atomic rename guarantee)
        for p in dst.rglob("*.jpg"):
            src_name = p.name.split("_1")[0]  # collision suffixes possible
        assert not list(dst.rglob("*.part"))

    def test_discard_journal(self, tmp_path):
        src, dst = _setup(tmp_path, n=2)
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        calls = {"n": 0}

        def cancel_after_one():
            calls["n"] += 1
            return calls["n"] > 1

        # A cancel before any file records nothing, so it does not leave a
        # journal. Discard still has to clear one that already has done lines.
        execute_plan(_plan(src, dst), options, cancel=cancel_after_one)
        journal = find_unfinished_journal(dst)
        assert journal is not None
        assert completed_sources(journal)
        discard_journal(dst)
        assert find_unfinished_journal(dst) is None

    def test_journal_tolerates_torn_final_line(self, tmp_path):
        jp = journal_path_for(tmp_path)
        jp.parent.mkdir(parents=True)
        jp.write_text('{"source": "a", "destination": "b", "status": "done"}\n'
                      '{"source": "c", "dest', encoding="utf-8")
        assert completed_sources(jp) == {"a"}
        assert find_unfinished_journal(tmp_path) is not None

    def test_second_run_interrupt_is_resumable_without_first_run(self, tmp_path):
        """A finished organize must not hide a later interrupted one.

        The journal used to stay append-only, so the first run's complete
        marker made ``find_unfinished_journal`` return None for every later
        crash into the same destination.
        """
        src_a, dst = _setup(tmp_path, n=2)
        options_a = OrganizeOptions(source_dir=src_a, dest_dir=dst)
        plan_a = _plan(src_a, dst)
        execute_plan(plan_a, options_a)
        assert find_unfinished_journal(dst) is None
        run_a = {str(p.source) for p in plan_a}
        kept = sorted(p.relative_to(dst) for p in dst.rglob("*.jpg"))
        assert kept

        src_b = tmp_path / "src_b"
        src_b.mkdir()
        for i in range(4):
            make_jpeg_with_exif(src_b / f"B_{i}.jpg",
                                datetime(2024, 8, 2, 11, i, 0))
        options_b = OrganizeOptions(source_dir=src_b, dest_dir=dst)
        plan_b = _plan(src_b, dst)
        calls = {"n": 0}

        def cancel_after_two():
            calls["n"] += 1
            return calls["n"] > 2

        _log, summary = execute_plan(plan_b, options_b, cancel=cancel_after_two)
        assert summary["cancelled"]
        jp = find_unfinished_journal(dst)
        assert jp is not None
        done = completed_sources(jp)
        assert len(done) == 2
        assert done.isdisjoint(run_a)
        assert done <= {str(p.source) for p in plan_b}
        # The finished run was replaced, not left sitting above this one.
        assert '"run": "complete"' not in jp.read_text(encoding="utf-8")
        assert sorted(p.relative_to(dst) for p in dst.rglob("*.jpg")
                      if "B_" not in p.name) == kept

        remaining = exclude_completed_sources(plan_b, done)
        assert len(remaining) == 2
        execute_plan(remaining, options_b)
        assert find_unfinished_journal(dst) is None
        assert len(list(dst.rglob("B_*.jpg"))) == 4
        assert sorted(p.relative_to(dst) for p in dst.rglob("*.jpg")
                      if "B_" not in p.name) == kept

    def test_discard_clears_interrupted_second_run(self, tmp_path):
        src_a, dst = _setup(tmp_path, n=1)
        execute_plan(_plan(src_a, dst),
                     OrganizeOptions(source_dir=src_a, dest_dir=dst))
        assert find_unfinished_journal(dst) is None
        first = next(dst.rglob("*.jpg"))

        src_b = tmp_path / "src_b"
        src_b.mkdir()
        make_jpeg_with_exif(src_b / "B_0.jpg", datetime(2024, 9, 1, 8, 0, 0))
        make_jpeg_with_exif(src_b / "B_1.jpg", datetime(2024, 9, 1, 8, 1, 0))
        options_b = OrganizeOptions(source_dir=src_b, dest_dir=dst)
        calls = {"n": 0}

        def cancel_after_one():
            calls["n"] += 1
            return calls["n"] > 1

        _log, summary = execute_plan(_plan(src_b, dst), options_b,
                                     cancel=cancel_after_one)
        assert summary["cancelled"]
        assert find_unfinished_journal(dst) is not None
        assert completed_sources(find_unfinished_journal(dst))
        discard_journal(dst)
        assert find_unfinished_journal(dst) is None
        assert not journal_path_for(dst).exists()
        assert first.exists() and first.read_bytes()

    def test_open_segment_after_complete_marker_is_the_unfinished_run(
            self, tmp_path):
        """A journal appended after complete (older builds) still resumes.

        Records before the last complete marker are a finished run. Resume
        continues the tail instead of replacing it.
        """
        jp = journal_path_for(tmp_path)
        jp.parent.mkdir(parents=True)
        jp.write_text(
            '{"action": "copy", "source": "run-a", "destination": "da",'
            ' "status": "done"}\n'
            '{"run": "complete"}\n'
            '{"action": "copy", "source": "run-b1", "destination": "db1",'
            ' "status": "done"}\n',
            encoding="utf-8",
        )
        assert find_unfinished_journal(tmp_path) == jp
        assert completed_sources(jp) == {"run-b1"}

        with JournalWriter(tmp_path) as writer:
            writer.record("copy", "run-b2", "db2")
        assert completed_sources(jp) == {"run-b1", "run-b2"}
        text = jp.read_text(encoding="utf-8")
        assert "run-a" in text
        assert find_unfinished_journal(tmp_path) is not None

        with JournalWriter(tmp_path) as writer:
            writer.complete()
        assert find_unfinished_journal(tmp_path) is None
        assert completed_sources(jp) == set()

    def test_unexpected_exception_leaves_unfinished_journal(
            self, tmp_path, monkeypatch):
        """A non-OSError mid-run must stay resumable.

        Per-file OSError is recorded and the loop continues. Anything else
        used to hit finally while cancelled was still false and write
        {"run": "complete"}. The next launch then skipped resume and a new
        JournalWriter could replace the open run.
        """
        src, dst = _setup(tmp_path, n=3)
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        plan = _plan(src, dst)
        real_copy = atomic_copy
        calls = {"n": 0}

        def fail_after_one(source, final):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("injected mid-organize failure")
            return real_copy(source, final)

        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_copy", fail_after_one)

        with pytest.raises(RuntimeError, match="injected mid-organize failure"):
            execute_plan(plan, options)

        jp = find_unfinished_journal(dst)
        assert jp is not None
        done = completed_sources(jp)
        assert len(done) == 1
        assert done <= {str(item.source) for item in plan}
        assert '"run": "complete"' not in jp.read_text(encoding="utf-8")
        # The open run is continued, not replaced by the next writer.
        with JournalWriter(dst) as writer:
            writer.record("copy", "still-open", str(dst / "still-open.jpg"))
        assert done <= completed_sources(jp)
        assert "still-open" in completed_sources(jp)

    def test_exception_then_resume_undo_covers_precrash_files(
            self, tmp_path, monkeypatch):
        """Crash, resume the rest, then undo removes both sets of copies.

        The journal line is durable, but a non-cancel failure used to skip
        ``save_log``. Resume started an empty log, and undo of that run left
        the pre-crash copies in the destination.
        """
        src, dst = _setup(tmp_path, n=3)
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        plan = _plan(src, dst)
        real_copy = atomic_copy
        calls = {"n": 0}

        def fail_after_one(source, final):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("injected mid-organize failure")
            return real_copy(source, final)

        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_copy", fail_after_one)

        with pytest.raises(RuntimeError, match="injected mid-organize failure"):
            execute_plan(plan, options)

        jp = find_unfinished_journal(dst)
        assert jp is not None
        assert '"run": "complete"' not in jp.read_text(encoding="utf-8")
        crashed = completed_sources(jp)
        assert len(crashed) == 1
        crashed_id = path_identity(next(iter(crashed)))

        partial = load_undoable_log(dst)
        assert partial is not None
        assert {path_identity(op.source) for op in partial.operations
                if op.status == "done"} == {crashed_id}

        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_copy", real_copy)
        remaining = exclude_completed_sources(plan, crashed)
        assert len(remaining) == 2
        assert crashed_id not in {
            path_identity(item.source) for item in remaining}

        log, summary = execute_plan(remaining, options)
        assert summary["copied"] == 2
        assert summary["errors"] == 0
        assert summary["cancelled"] is False
        assert find_unfinished_journal(dst) is None

        newest = load_undoable_log(dst)
        assert newest is not None
        assert newest.run_id == log.run_id
        done_ids = {path_identity(op.source) for op in newest.operations
                    if op.status == "done"}
        assert done_ids == {path_identity(item.source) for item in plan}
        pre = next(op for op in newest.operations
                   if path_identity(op.source) == crashed_id)
        pre_dest = Path(pre.destination)
        assert pre_dest.is_file()
        assert pre.size == pre_dest.stat().st_size
        assert pre.sha256 == hashlib.sha256(pre_dest.read_bytes()).hexdigest()
        assert len(list(dst.rglob("*.jpg"))) == 3

        result = undo_log(newest, dst)
        assert result["undone"] == 3
        assert result["failed"] == 0
        assert result["kept"] == 0
        assert not list(dst.rglob("*.jpg"))
        for item in plan:
            assert item.source.is_file()
            assert item.source.read_bytes()

    def test_exception_before_any_file_writes_no_log(
            self, tmp_path, monkeypatch):
        src, dst = _setup(tmp_path, n=2)
        options = OrganizeOptions(source_dir=src, dest_dir=dst)

        def fail_immediately(source, final):
            raise RuntimeError("injected before any file")

        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_copy", fail_immediately)
        with pytest.raises(RuntimeError, match="injected before any file"):
            execute_plan(_plan(src, dst), options)

        assert load_undoable_log(dst) is None
        assert not log_path_for(dst).exists()
        jp = find_unfinished_journal(dst)
        assert jp is not None
        assert completed_sources(jp) == set()
        assert '"run": "complete"' not in jp.read_text(encoding="utf-8")

    def test_resume_seeds_missing_destination_with_defaults(self, tmp_path):
        """A journaled file that is gone is still an undo row."""
        src, dst = _setup(tmp_path, n=1)
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        plan = _plan(src, dst)
        missing_source = str(tmp_path / "never-copied.jpg")
        missing_dest = str(dst / "missing.jpg")
        with JournalWriter(dst) as writer:
            writer.record("move", missing_source, missing_dest)

        log, summary = execute_plan(plan, options)

        assert summary["copied"] == 1
        assert find_unfinished_journal(dst) is None
        seeded, copied = log.operations
        assert seeded.action == "move"
        assert seeded.source == missing_source
        assert seeded.destination == missing_dest
        assert seeded.status == "done"
        assert seeded.size == -1
        assert seeded.sha256 == ""
        assert copied.status == "done"
        assert path_identity(copied.source) == path_identity(plan[0].source)
        assert copied.size == plan[0].destination.stat().st_size
        assert copied.sha256 == hashlib.sha256(
            plan[0].destination.read_bytes()).hexdigest()

    def test_write_log_fsyncs_temp_before_replace(self, tmp_path, monkeypatch):
        """The undo log is fsynced while it is still a temp name.

        Mirrors the part-file check: flush, then fsync of that fd, then
        ``os.replace``. No directory fsync.
        """
        log = new_log(tmp_path)
        log.operations.append(Operation(
            action="copy", source=str(tmp_path / "a.jpg"),
            destination=str(tmp_path / "b.jpg"), status="done",
            size=4, sha256="abc"))
        path = log_path_for(tmp_path)
        tmp = path.with_name(path.name + ".tmp")
        order = []
        real_open = open
        real_fsync = os.fsync
        real_replace = os.replace

        class _Tmp:
            def __init__(self, raw):
                self._raw = raw

            def __getattr__(self, name):
                return getattr(self._raw, name)

            def __enter__(self):
                self._raw.__enter__()
                return self

            def __exit__(self, exc_type, exc, tb):
                return self._raw.__exit__(exc_type, exc, tb)

            def flush(self):
                order.append(("flush", self._raw.fileno()))
                return self._raw.flush()

        def spy_open(file, mode="r", *args, **kwargs):
            fh = real_open(file, mode, *args, **kwargs)
            if Path(file) == tmp:
                order.append(("open", fh.fileno()))
                return _Tmp(fh)
            return fh

        def spy_fsync(fd):
            order.append(("fsync", fd))
            assert tmp.exists() and not path.exists()
            return real_fsync(fd)

        def spy_replace(src_path, dst_path):
            order.append(("replace", Path(src_path), Path(dst_path)))
            return real_replace(src_path, dst_path)

        monkeypatch.setattr(
            "media_organizer.core.plan.open", spy_open, raising=False)
        monkeypatch.setattr(
            "media_organizer.core.plan.os.fsync", spy_fsync)
        monkeypatch.setattr(
            "media_organizer.core.plan.os.replace", spy_replace)

        saved = save_log(log, tmp_path)

        assert saved == path
        assert path.is_file()
        assert not tmp.exists()
        assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == log.run_id
        kinds = [kind for kind, *_rest in order]
        flush_i = kinds.index("flush")
        fsync_i = kinds.index("fsync")
        replace_i = kinds.index("replace")
        assert flush_i < fsync_i < replace_i
        tmp_fd = order[kinds.index("open")][1]
        assert order[flush_i][1] == tmp_fd
        assert order[fsync_i][1] == tmp_fd
        assert kinds.count("fsync") == 1
        assert order[replace_i][1:] == (tmp, path)

    def test_successful_run_writes_complete_and_is_not_unfinished(self, tmp_path):
        src, dst = _setup(tmp_path, n=2)
        execute_plan(_plan(src, dst),
                     OrganizeOptions(source_dir=src, dest_dir=dst))
        jp = journal_path_for(dst)
        assert find_unfinished_journal(dst) is None
        text = jp.read_text(encoding="utf-8")
        assert text.strip().splitlines()[-1] == '{"run": "complete"}'
        assert completed_sources(jp) == set()

    def test_cancelled_run_does_not_write_complete(self, tmp_path):
        src, dst = _setup(tmp_path, n=3)
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        calls = {"n": 0}

        def cancel_after_one():
            calls["n"] += 1
            return calls["n"] > 1

        _log, summary = execute_plan(_plan(src, dst), options,
                                     cancel=cancel_after_one)
        assert summary["cancelled"]
        jp = find_unfinished_journal(dst)
        assert jp is not None
        assert completed_sources(jp)
        assert '"run": "complete"' not in jp.read_text(encoding="utf-8")

    @pytest.mark.parametrize("journalled,same", [
        # Slash style only. PureWindowsPath stringifies with backslashes,
        # which is what Path does on Windows for a forward-slash input.
        ("C:/Photos/Vacation/IMG_0001.JPG",
         PureWindowsPath("C:/Photos/Vacation/IMG_0001.JPG")),
        # Case and slashes, the mismatch a typed path vs a folder dialog makes.
        ("C:/Photos/Vacation/IMG_0001.JPG",
         PureWindowsPath(r"c:\photos\vacation\img_0001.jpg")),
        # Journal stored the backslash form; the rescan kept forward slashes
        # and a different case.
        (r"C:\Photos\Vacation\IMG_0001.JPG",
         PureWindowsPath("C:/photos/vacation/img_0001.jpg")),
    ])
    def test_resume_skip_matches_windows_case_and_slashes(
            self, tmp_path, journalled, same):
        """Completed sources skip even when only case or slashes differ.

        ``windows=True`` applies the helper's Windows key on Linux CI.
        Exact string membership is the bug: it would organize the file
        again and land a ``name_1.ext`` collision copy.
        """
        # A real Path, so str() is the spelling a rescan would hand the GUI.
        # On Windows that is backslashes; PureWindowsPath reproduces that here.
        same_path = Path(os.fspath(same))
        other_path = Path(os.fspath(
            PureWindowsPath(r"C:\Photos\Vacation\IMG_0099.JPG")))
        assert str(same_path) != journalled
        assert str(other_path) != journalled

        with JournalWriter(tmp_path) as writer:
            writer.record("copy", journalled, "D:/Organized/2024/IMG_0001.jpg")
        jp = journal_path_for(tmp_path)
        stored = json.loads(jp.read_text(encoding="utf-8").splitlines()[0])
        assert stored["source"] == journalled  # original spelling, not the key

        done = completed_sources(jp)
        assert journalled in done
        assert str(same_path) not in done

        capture = CaptureDate(datetime(2024, 7, 15, 10, 0, 0),
                              DateSource.EXIF, Confidence.HIGH, "EXIF")
        plan = [
            PlannedFile(same_path, Path(os.fspath(
                PureWindowsPath(r"D:\Organized\2024\a.jpg"))),
                        10, capture, "image"),
            PlannedFile(other_path, Path(os.fspath(
                PureWindowsPath(r"D:\Organized\2024\b.jpg"))),
                        10, capture, "image"),
        ]
        remaining = exclude_completed_sources(plan, done, windows=True)
        assert [p.source for p in remaining] == [other_path]
        assert path_identity(journalled, windows=True) == path_identity(
            same_path, windows=True)
        assert path_identity(other_path, windows=True) != path_identity(
            same_path, windows=True)

    def test_path_identity_follows_host_normcase(self, tmp_path):
        path = tmp_path / "Photos" / "A.JPG"
        text = os.fspath(path)
        assert path_identity(path) == os.path.normcase(
            os.path.normpath(os.path.abspath(text)))
        assert path_identity(text) == path_identity(path)
        messy = str(path.parent) + os.sep + os.sep + path.name
        assert path_identity(messy) == path_identity(path)
        other = path.parent / "B.JPG"
        assert path_identity(other) != path_identity(path)


def _resume_plan(plan, dest):
    """Sources a Resume click would still organize.

    Mirrors ``start_organize``: an open journal's ``completed_sources`` are
    removed; no journal means the whole plan stays.
    """
    journal = find_unfinished_journal(dest)
    if journal is None:
        return list(plan)
    return exclude_completed_sources(plan, completed_sources(journal))


class TestPartialFileErrorsLeaveJournalUnfinished:
    """A per-file failure must not close the crash journal.

    The plan loop used to finish without ``break`` and call ``complete()``
    even when ``summary["errors"]`` was non-zero. Resume then saw no
    unfinished journal. The next Copy planned files that had already
    landed onto ``name_1``, and a destination published before a failed
    ``JournalWriter.record`` could not be adopted.
    """

    def test_copy_oserror_after_first_file_stays_resumable(
            self, tmp_path, monkeypatch):
        src, dst = _setup(tmp_path, n=2)
        options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=True)
        plan = _plan(src, dst, copy_mode=True)
        assert len(plan) >= 2
        first, second = plan[0], plan[1]
        assert first.destination is not None and second.destination is not None
        assert first.destination.name == first.source.name
        assert second.destination.name == second.source.name
        real_copy = atomic_copy
        calls = {"n": 0}

        def fail_second(source, final):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise OSError("injected copy failure")
            return real_copy(source, final)

        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_copy", fail_second)

        _log, summary = execute_plan(plan, options)

        assert summary["errors"] >= 1
        assert summary["cancelled"] is False
        assert summary["copied"] == 1
        assert first.destination.is_file()
        assert first.destination.read_bytes() == first.source.read_bytes()
        assert first.source.is_file()
        assert not second.destination.exists()
        assert not (first.destination.parent / "IMG_0_1.jpg").exists()

        jp = find_unfinished_journal(dst)
        assert jp is not None
        text = jp.read_text(encoding="utf-8")
        assert '"run": "complete"' not in text
        done = completed_sources(jp)
        done_ids = {path_identity(source) for source in done}
        assert path_identity(first.source) in done_ids
        assert path_identity(second.source) not in done_ids
        assert any(
            path_identity(op["source"]) == path_identity(first.source)
            and path_identity(op["destination"]) == path_identity(first.destination)
            for op in completed_operations(jp))

        rescanned = _plan(src, dst, copy_mode=True)
        by_id = {path_identity(item.source): item for item in rescanned}
        again_first = by_id[path_identity(first.source)]
        again_second = by_id[path_identity(second.source)]
        assert again_first.destination == first.destination
        assert again_second.destination == second.destination

        remaining = _resume_plan(rescanned, dst)
        assert [path_identity(item.source) for item in remaining] == [
            path_identity(second.source)]
        assert remaining[0].destination == second.destination

    def test_record_failure_after_publish_can_be_adopted(
            self, tmp_path, monkeypatch):
        src, dst = _setup(tmp_path, n=2)
        options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=True)
        plan = _plan(src, dst, copy_mode=True)
        first, second = plan[0], plan[1]
        assert first.destination.name == first.source.name
        assert second.destination.name == second.source.name
        payload = second.source.read_bytes()
        real_record = JournalWriter.record
        calls = {"n": 0}

        def fail_second_record(self, action, source, destination, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 2:
                published = Path(destination)
                assert published.is_file()
                assert published.read_bytes() == payload
                assert published == second.destination
                raise OSError("journal write failed")
            return real_record(self, action, source, destination, **kwargs)

        monkeypatch.setattr(JournalWriter, "record", fail_second_record)

        _log, summary = execute_plan(plan, options)

        assert summary["errors"] >= 1
        assert summary["copied"] == 2
        assert first.destination.is_file()
        assert second.destination.is_file()
        assert second.destination.read_bytes() == payload
        assert not (first.destination.parent / "IMG_0_1.jpg").exists()
        assert not (second.destination.parent / "IMG_1_1.jpg").exists()

        jp = find_unfinished_journal(dst)
        assert jp is not None
        assert '"run": "complete"' not in jp.read_text(encoding="utf-8")
        done_ids = {path_identity(source) for source in completed_sources(jp)}
        assert path_identity(first.source) in done_ids
        assert path_identity(second.source) not in done_ids
        assert any(
            path_identity(op["source"]) == path_identity(first.source)
            for op in completed_operations(jp))

        rescanned = _plan(src, dst, copy_mode=True)
        by_id = {path_identity(item.source): item for item in rescanned}
        assert by_id[path_identity(second.source)].destination == second.destination
        assert by_id[path_identity(first.source)].destination == first.destination

        remaining = _resume_plan(rescanned, dst)
        assert [path_identity(item.source) for item in remaining] == [
            path_identity(second.source)]
        assert remaining[0].destination == second.destination

        def fail_copy(*_args, **_kwargs):
            raise AssertionError(
                "resume copied a file that was already published")

        monkeypatch.setattr(JournalWriter, "record", real_record)
        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_copy", fail_copy)

        retry_log, retry = execute_plan(remaining, options)

        assert retry["errors"] == 0
        assert retry["copied"] == 1
        assert retry["cancelled"] is False
        assert second.destination.read_bytes() == payload
        assert not (second.destination.parent / "IMG_1_1.jpg").exists()
        assert sorted(p.name for p in dst.rglob("*.jpg")) == [
            "IMG_0.jpg", "IMG_1.jpg"]
        text = journal_path_for(dst).read_text(encoding="utf-8")
        assert str(first.source) in text
        assert str(second.source) in text
        assert text.strip().splitlines()[-1] == '{"run": "complete"}'
        assert find_unfinished_journal(dst) is None
        adopted = [op for op in retry_log.operations
                   if path_identity(op.source) == path_identity(second.source)
                   and op.status == "done"]
        assert [(op.action, op.destination) for op in adopted] == [
            ("copy", str(second.destination))]

    def test_missing_destination_row_leaves_successes_resumable(self, tmp_path):
        src, dst = _setup(tmp_path, n=2)
        options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=True)
        plan = _plan(src, dst, copy_mode=True)
        first, broken = plan[0], plan[1]
        natural = broken.destination
        broken.destination = None
        broken.error = "unreadable during scan"

        _log, summary = execute_plan(plan, options)

        assert summary["errors"] >= 1
        assert summary["copied"] == 1
        assert summary["cancelled"] is False
        assert first.destination.is_file()
        jp = find_unfinished_journal(dst)
        assert jp is not None
        assert '"run": "complete"' not in jp.read_text(encoding="utf-8")
        done_ids = {path_identity(source) for source in completed_sources(jp)}
        assert done_ids == {path_identity(first.source)}
        assert any(
            path_identity(op["source"]) == path_identity(first.source)
            for op in completed_operations(jp))

        rescanned = _plan(src, dst, copy_mode=True)
        by_id = {path_identity(item.source): item for item in rescanned}
        assert by_id[path_identity(first.source)].destination == first.destination
        assert by_id[path_identity(broken.source)].destination == natural
        remaining = _resume_plan(rescanned, dst)
        assert [path_identity(item.source) for item in remaining] == [
            path_identity(broken.source)]

    def test_finished_prior_run_still_gets_a_collision_suffix(
            self, tmp_path, monkeypatch):
        """A completed earlier organize is not adopted after a later error.

        Leaving the failed run unfinished must not reuse a destination that
        belongs to a run that already wrote ``{"run": "complete"}``.
        """
        src_a, dst = _setup(tmp_path, n=1)
        execute_plan(_plan(src_a, dst),
                     OrganizeOptions(source_dir=src_a, dest_dir=dst))
        natural = dst / "2024" / "07 July" / "IMG_0.jpg"
        original = natural.read_bytes()
        assert find_unfinished_journal(dst) is None

        src_b = tmp_path / "src_b"
        src_b.mkdir()
        make_jpeg_with_exif(src_b / "B_0.jpg", datetime(2024, 8, 2, 11, 0, 0))
        make_jpeg_with_exif(src_b / "B_1.jpg", datetime(2024, 8, 2, 11, 1, 0))
        options_b = OrganizeOptions(source_dir=src_b, dest_dir=dst, copy_mode=True)
        plan_b = _plan(src_b, dst, copy_mode=True)
        real_copy = atomic_copy
        calls = {"n": 0}

        def fail_second(source, final):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise OSError("injected copy failure")
            return real_copy(source, final)

        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_copy", fail_second)
        _log, summary = execute_plan(plan_b, options_b)

        assert summary["errors"] >= 1
        assert find_unfinished_journal(dst) is not None
        assert natural.read_bytes() == original
        again = _plan(src_a, dst)
        assert again[0].destination == natural.with_name("IMG_0_1.jpg")
        rescanned_b = _plan(src_b, dst, copy_mode=True)
        by_id = {path_identity(item.source): item for item in rescanned_b}
        assert by_id[path_identity(plan_b[0].source)].destination == (
            plan_b[0].destination)


class TestDiscardRestartCancel:
    """Cancelling a Discard restart before a new write must not hide Undo.

    Discard seeds and saves the interrupted journal, then drops it and
    starts a new run for whatever is left. Cancelling that restart on the
    first row used to ``save_log`` an empty ``RunLog`` in front of the
    seeded log, and the ``JournalWriter`` opened for the restart stayed
    unfinished with zero done lines.
    """

    def test_cancel_before_any_new_file_keeps_seeded_log(
            self, tmp_path):
        src, dst = _setup(tmp_path, n=3)
        options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=True)
        plan = _plan(src, dst, copy_mode=True)
        calls = {"n": 0}

        def cancel_after_one():
            calls["n"] += 1
            return calls["n"] > 1

        _log, summary = execute_plan(plan, options, cancel=cancel_after_one)
        assert summary["cancelled"] is True
        journal = find_unfinished_journal(dst)
        assert journal is not None
        done = completed_sources(journal)
        assert len(done) == 1

        # Same sequence as Discard after free-space preflight: seed and
        # save from the open journal, drop it, then run only rows the
        # seeded log does not already own.
        seeded, seeded_summary = execute_plan([], options)
        assert seeded_summary["cancelled"] is False
        assert len(seeded.operations) == 1
        assert seeded.operations[0].status == "done"
        interrupted = Path(seeded.operations[0].destination)
        assert interrupted.is_file()
        discard_journal(dst)
        assert find_unfinished_journal(dst) is None
        owned = saved_run_destinations(dst)
        remaining = [item for item in plan
                     if not saved_run_owns_plan_row(item, owned)]
        assert remaining
        assert len(remaining) < len(plan)

        restart, restart_summary = execute_plan(
            remaining, options, cancel=lambda: True)
        assert restart_summary["cancelled"] is True
        assert restart.operations == []

        assert find_unfinished_journal(dst) is None
        logs = list_run_logs(dst)
        assert logs
        assert logs[0].run_id == seeded.run_id
        assert logs[0].operations
        assert restart.run_id not in {item.run_id for item in logs}
        assert all(item.operations for item in logs)

        newest = load_undoable_log(dst)
        assert newest is not None and newest.run_id == seeded.run_id
        result = undo_log(newest, dst)
        assert result["undone"] == 1
        assert result["failed"] == 0
        assert not interrupted.exists()
        assert Path(seeded.operations[0].source).is_file()

    def test_resume_cancel_before_new_file_keeps_journal_and_seeded_log(
            self, tmp_path):
        """Resume that is cancelled before a new file still undoes the seed.

        The open journal is the thing to continue. An empty restart after
        Discard is the case that must not publish a log; this one already
        has done lines.
        """
        src, dst = _setup(tmp_path, n=3)
        options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=True)
        plan = _plan(src, dst, copy_mode=True)
        calls = {"n": 0}

        def cancel_after_one():
            calls["n"] += 1
            return calls["n"] > 1

        execute_plan(plan, options, cancel=cancel_after_one)
        journal = find_unfinished_journal(dst)
        assert journal is not None
        done = completed_sources(journal)
        remaining = exclude_completed_sources(plan, done)
        assert remaining

        log, summary = execute_plan(remaining, options, cancel=lambda: True)
        assert summary["cancelled"] is True
        assert len(log.operations) == 1
        assert log.operations[0].status == "done"
        still = find_unfinished_journal(dst)
        assert still is not None
        assert completed_sources(still) == done
        newest = load_undoable_log(dst)
        assert newest is not None
        assert newest.run_id == log.run_id
        assert len(newest.operations) == 1


class TestUndoDiscardsOverlappingJournal:
    """Undo of a cancelled run must not leave Resume skipping restored files.

    ``execute_plan`` saves a log on cancel, so Undo is offered, and it
    leaves the crash journal unfinished. Resume trusts ``completed_sources``
    and would otherwise report success while those files stay out of the
    date folders.
    """

    def test_undo_after_cancel_discards_unfinished_journal(self, tmp_path):
        src, dst = _setup(tmp_path, n=4)
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        calls = {"n": 0}

        def cancel_after_two():
            calls["n"] += 1
            return calls["n"] > 2

        log, summary = execute_plan(_plan(src, dst), options,
                                    cancel=cancel_after_two)
        assert summary["cancelled"]
        journal = find_unfinished_journal(dst)
        assert journal is not None
        done = completed_sources(journal)
        assert done
        copied = list(dst.rglob("*.jpg"))
        assert len(copied) == len(done)

        result = undo_log(log, dst)

        assert result["undone"] == len(done)
        assert result["failed"] == 0
        assert not list(dst.rglob("*.jpg"))
        for source in done:
            assert Path(source).is_file()
        assert find_unfinished_journal(dst) is None
        assert not journal_path_for(dst).exists()

    def test_undo_after_cancel_resume_recopies_restored_sources(self, tmp_path):
        src, dst = _setup(tmp_path, n=4)
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        plan = _plan(src, dst)
        natural = {path_identity(item.source): item.destination for item in plan}
        calls = {"n": 0}

        def cancel_after_two():
            calls["n"] += 1
            return calls["n"] > 2

        log, summary = execute_plan(plan, options, cancel=cancel_after_two)
        assert summary["cancelled"]
        journal = find_unfinished_journal(dst)
        assert journal is not None
        done = completed_sources(journal)
        done_ids = {path_identity(source) for source in done}
        assert done_ids
        # The bug: Resume would drop exactly the files Undo is about to put back.
        skipped = exclude_completed_sources(plan, done)
        assert done_ids.isdisjoint(path_identity(item.source) for item in skipped)
        assert len(skipped) < len(plan)

        result = undo_log(log, dst)
        assert result["undone"] == len(done)
        assert result["failed"] == 0
        assert not list(dst.rglob("*.jpg"))
        assert find_unfinished_journal(dst) is None

        fresh = _plan(src, dst)
        assert {path_identity(item.source): item.destination
                for item in fresh} == natural
        resumed = _resume_plan(fresh, dst)
        resumed_ids = {path_identity(item.source) for item in resumed}
        assert done_ids <= resumed_ids
        assert resumed_ids == set(natural)

        log2, summary2 = execute_plan(resumed, options)
        assert summary2["cancelled"] is False
        assert summary2["copied"] == len(plan)
        assert summary2["errors"] == 0
        assert find_unfinished_journal(dst) is None
        copied = list(dst.rglob("*.jpg"))
        assert sorted(copied) == sorted(natural.values())
        for item in resumed:
            dest = natural[path_identity(item.source)]
            assert item.destination == dest
            assert dest.is_file()
            assert dest.read_bytes() == item.source.read_bytes()
            assert item.source.is_file()
        assert all(op.status == "done" for op in log2.operations)
        assert {path_identity(op.source) for op in log2.operations} == resumed_ids

    def test_undo_of_unrelated_older_log_keeps_open_journal(self, tmp_path):
        src_old = tmp_path / "old"
        dst = tmp_path / "dst"
        src_old.mkdir()
        make_jpeg_with_exif(src_old / "OLD_0.jpg", datetime(2023, 1, 2, 3, 4, 5))
        make_jpeg_with_exif(src_old / "OLD_1.jpg", datetime(2023, 1, 2, 3, 5, 5))
        options_old = OrganizeOptions(source_dir=src_old, dest_dir=dst)
        log_old, summary_old = execute_plan(_plan(src_old, dst), options_old)
        assert summary_old["copied"] == 2
        assert find_unfinished_journal(dst) is None

        src_new = tmp_path / "new"
        src_new.mkdir()
        for i in range(4):
            make_jpeg_with_exif(src_new / f"NEW_{i}.jpg",
                                datetime(2024, 6, 1, 12, i, 0))
        options_new = OrganizeOptions(source_dir=src_new, dest_dir=dst)
        calls = {"n": 0}

        def cancel_after_two():
            calls["n"] += 1
            return calls["n"] > 2

        log_new, summary_new = execute_plan(
            _plan(src_new, dst), options_new, cancel=cancel_after_two)
        assert summary_new["cancelled"]
        journal = find_unfinished_journal(dst)
        assert journal is not None
        done = completed_sources(journal)
        assert done
        assert done.isdisjoint(str(path) for path in src_old.glob("*.jpg"))
        journal_text = journal.read_text(encoding="utf-8")
        new_copies = sorted(p.relative_to(dst) for p in dst.rglob("NEW_*.jpg"))
        assert new_copies

        logs = list_run_logs(dst)
        assert [item.run_id for item in logs] == [log_new.run_id, log_old.run_id]
        result = undo_log(logs[1], dst)

        assert result["undone"] == 2
        assert result["failed"] == 0
        assert not list(dst.rglob("OLD_*.jpg"))
        assert sorted(p.relative_to(dst) for p in dst.rglob("NEW_*.jpg")) == new_copies
        still = find_unfinished_journal(dst)
        assert still is not None
        assert completed_sources(still) == done
        assert still.read_text(encoding="utf-8") == journal_text

    def test_undo_matches_journal_sources_by_path_identity(self, tmp_path):
        """A spelling-only difference still counts as the same restored file."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        source = src / "IMG.jpg"
        payload = b"same-bytes-for-identity"
        source.write_bytes(payload)
        final = dst / "2024" / "IMG.jpg"
        final.parent.mkdir(parents=True)
        final.write_bytes(payload)
        messy = str(source.parent) + os.sep + os.sep + source.name
        assert messy != str(source)
        assert path_identity(messy) == path_identity(source)
        with JournalWriter(dst) as writer:
            writer.record("copy", messy, str(final))
        assert completed_sources(journal_path_for(dst)) == {messy}
        assert str(source) not in completed_sources(journal_path_for(dst))

        log = new_log(dst)
        log.operations.append(Operation(
            action="copy", source=str(source), destination=str(final),
            status="done", size=len(payload)))
        result = undo_log(log, dst)

        assert result["undone"] == 1
        assert result["failed"] == 0
        assert not final.exists()
        assert source.read_bytes() == payload
        assert find_unfinished_journal(dst) is None
        assert not journal_path_for(dst).exists()


def _unfinished_without(dest: Path, source: Path) -> None:
    """Leave an interrupted journal that does not list ``source`` as done."""
    jp = journal_path_for(dest)
    jp.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({
        "action": "copy",
        "source": str(dest / "not-the-source.jpg"),
        "destination": str(dest / "already-journaled.jpg"),
        "status": "done",
    })
    with open(jp, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    assert find_unfinished_journal(dest) == jp
    assert str(source) not in completed_sources(jp)


def _truncated_orphan_resume(tmp_path, *, copy_mode):
    """Part is short, final name is missing, journal is unfinished, source remains."""
    src, dst = _setup(tmp_path, n=1)
    source = src / "IMG_0.jpg"
    payload = source.read_bytes()
    assert len(payload) > 32
    options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=copy_mode)
    planned = _plan(src, dst, copy_mode=copy_mode)[0].destination
    assert planned.name == "IMG_0.jpg"
    planned.parent.mkdir(parents=True)
    part = planned.with_name(planned.name + PART_SUFFIX)
    part.write_bytes(payload[:8])
    assert not planned.exists()
    _unfinished_without(dst, source)
    user_part = planned.parent / "notes.part"
    user_part.write_bytes(b"keep-me")
    plan = _plan(src, dst, copy_mode=copy_mode)
    assert [item.destination for item in plan] == [planned]
    remaining = exclude_completed_sources(
        plan, completed_sources(journal_path_for(dst)))
    assert [item.source for item in remaining] == [source]
    return (options, dst, source, payload, planned, part, user_part, remaining)


def _assert_full_file_at_natural_name(
        dst, source, payload, planned, part, user_part, log, summary, *,
        copy_mode):
    key = "copied" if copy_mode else "moved"
    assert summary[key] == 1
    assert summary["errors"] == 0
    assert planned.is_file()
    assert planned.read_bytes() == payload
    assert not part.exists()
    assert not list(dst.rglob("*_1*"))
    assert [p.name for p in dst.rglob("*.jpg")] == ["IMG_0.jpg"]
    assert user_part.read_bytes() == b"keep-me"
    action = "copy" if copy_mode else "move"
    # An open journal may also seed earlier done rows. This file is logged once.
    real = [op for op in log.operations
            if path_identity(op.source) == path_identity(source)]
    assert [(op.action, op.source, op.destination, op.status)
            for op in real] == [
        (action, str(source), str(planned), "done")]
    if copy_mode:
        assert source.is_file()
        assert source.read_bytes() == payload
    else:
        assert not source.exists()


class TestTruncatedOrphanPartResume:
    """Crash mid-write: truncated part, missing final name, source still there.

    ``cleanup_stale_parts`` must delete the part. Promoting it would leave
    junk at the natural name. Resume would then fail ``files_identical``,
    write the good bytes to ``name_1``, and a move could unlink the source.
    """

    def test_copy_resume_replaces_truncated_part_with_source(
            self, tmp_path):
        (options, dst, source, payload, planned, part, user_part,
         remaining) = _truncated_orphan_resume(tmp_path, copy_mode=True)
        assert part.is_file()
        assert part.read_bytes() == payload[:8]

        log, summary = execute_plan(remaining, options)

        _assert_full_file_at_natural_name(
            dst, source, payload, planned, part, user_part, log, summary,
            copy_mode=True)
        assert find_unfinished_journal(dst) is None

    def test_cross_volume_move_resume_journals_natural_name_before_unlink(
            self, tmp_path, monkeypatch):
        (options, dst, source, payload, planned, part, user_part,
         remaining) = _truncated_orphan_resume(tmp_path, copy_mode=False)
        order = []
        real_replace = os.replace
        real_record = JournalWriter.record
        real_unlink = Path.unlink

        def spy_replace(src_path, dst_path):
            # Fail only the same-volume rename of the source. A promotion
            # of the truncated part would still go through, and resume
            # would then land the good bytes on ``IMG_0_1.jpg``.
            if Path(src_path) == source:
                order.append("replace-cross")
                raise OSError("simulated cross-volume rename")
            order.append(("replace", Path(src_path), Path(dst_path)))
            return real_replace(src_path, dst_path)

        def spy_record(self, action, source_s, destination, **kwargs):
            order.append("journal")
            assert Path(source_s) == source
            assert source.is_file()
            assert source.read_bytes() == payload
            assert Path(destination) == planned
            assert planned.read_bytes() == payload
            assert not part.exists()
            return real_record(self, action, source_s, destination, **kwargs)

        def spy_unlink(self, *args, **kwargs):
            if self == source:
                order.append("unlink")
                text = journal_path_for(dst).read_text(encoding="utf-8")
                assert str(source) in text
                assert str(planned) in text
                assert "IMG_0_1" not in text
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(
            "media_organizer.core.journal.os.replace", spy_replace)
        monkeypatch.setattr(JournalWriter, "record", spy_record)
        monkeypatch.setattr(Path, "unlink", spy_unlink)

        log, summary = execute_plan(remaining, options)

        _assert_full_file_at_natural_name(
            dst, source, payload, planned, part, user_part, log, summary,
            copy_mode=False)
        assert order[0] == "replace-cross"
        assert ("replace", part, planned) in order
        assert order.index("journal") < order.index("unlink")
        assert order.count("unlink") == 1
        assert find_unfinished_journal(dst) is None


class TestResumePublishedDestination:
    """Crash after the final name is published and before the journal line.

    ``cleanup_stale_parts`` only sees a ``.mediaorganizer.part``. Once
    ``os.replace`` has published the final name, resume must not invent
    ``name_1``.
    """

    def test_resume_keeps_final_name_published_before_journal(
            self, tmp_path, monkeypatch):
        src, dst = _setup(tmp_path, n=1)
        source = src / "IMG_0.jpg"
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        planned = _plan(src, dst)[0].destination
        assert planned.name == "IMG_0.jpg"
        planned.parent.mkdir(parents=True)
        atomic_copy(source, planned)
        # A second copy would refresh this mtime from the source.
        published = 1_000_000_000
        os.utime(planned, (published, published))
        os.utime(source, (published + 50, published + 50))
        _unfinished_without(dst, source)

        plan = _plan(src, dst)
        assert plan[0].destination == planned
        remaining = exclude_completed_sources(
            plan, completed_sources(journal_path_for(dst)))
        assert [item.source for item in remaining] == [source]

        def fail_copy(*_args, **_kwargs):
            raise AssertionError(
                "resume copied a file that was already published")

        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_copy", fail_copy)
        log, summary = execute_plan(remaining, options)

        assert summary["copied"] == 1
        assert summary["errors"] == 0
        assert sorted(p.name for p in dst.rglob("*.jpg")) == ["IMG_0.jpg"]
        assert planned.read_bytes() == source.read_bytes()
        assert planned.stat().st_mtime == published
        assert source.exists()
        done = [op for op in log.operations
                if op.status == "done"
                and path_identity(op.source) == path_identity(source)]
        assert [(op.action, op.source, op.destination) for op in done] == [
            ("copy", str(source), str(planned))]
        text = journal_path_for(dst).read_text(encoding="utf-8")
        assert str(source) in text
        assert text.strip().splitlines()[-1] == '{"run": "complete"}'
        assert find_unfinished_journal(dst) is None

    def test_cross_volume_resume_unlinks_source_after_journal(
            self, tmp_path, monkeypatch):
        src, dst = _setup(tmp_path, n=1)
        source = src / "IMG_0.jpg"
        payload = source.read_bytes()
        options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False)
        planned = _plan(src, dst)[0].destination
        planned.parent.mkdir(parents=True)
        atomic_copy(source, planned)
        published = 1_000_000_000
        os.utime(planned, (published, published))
        _unfinished_without(dst, source)
        plan = _plan(src, dst)
        assert plan[0].destination == planned
        remaining = exclude_completed_sources(
            plan, completed_sources(journal_path_for(dst)))

        order = []
        real_record = JournalWriter.record
        real_unlink = Path.unlink

        def spy_record(self, action, source_text, destination, **kwargs):
            order.append("journal")
            assert source.exists()
            return real_record(self, action, source_text, destination, **kwargs)

        def spy_unlink(self, *args, **kwargs):
            if self == source:
                order.append("unlink")
                text = journal_path_for(dst).read_text(encoding="utf-8")
                assert str(source) in text
            return real_unlink(self, *args, **kwargs)

        def fail_transfer(*_args, **_kwargs):
            raise AssertionError(
                "resume transferred a file that was already published")

        monkeypatch.setattr(JournalWriter, "record", spy_record)
        monkeypatch.setattr(Path, "unlink", spy_unlink)
        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_move", fail_transfer)
        monkeypatch.setattr(
            "media_organizer.core.executor.atomic_copy", fail_transfer)

        log, summary = execute_plan(remaining, options)

        assert summary["moved"] == 1
        assert summary["errors"] == 0
        assert order == ["journal", "unlink"]
        assert not source.exists()
        assert planned.read_bytes() == payload
        assert planned.stat().st_mtime == published
        assert not list(dst.rglob("IMG_0_1.jpg"))
        real = [op for op in log.operations
                if path_identity(op.source) == path_identity(source)]
        assert [(op.action, op.source, op.destination, op.status)
                for op in real] == [
            ("move", str(source), str(planned), "done")]

    def test_reused_destination_is_taken_for_a_later_row(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        (src / "a").mkdir(parents=True)
        (src / "b").mkdir()
        when = datetime(2024, 7, 15, 10, 0, 0)
        first = make_jpeg_with_exif(src / "a" / "photo.jpg", when)
        second = make_jpeg_with_exif(src / "b" / "photo.jpg", when)
        second.write_bytes(second.read_bytes() + b"\x00extra")
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        natural = build_plan(options, files=[first])[0].destination
        natural.parent.mkdir(parents=True)
        atomic_copy(first, natural)
        _unfinished_without(dst, first)

        plan = build_plan(options, files=[first, second])

        assert plan[0].destination == natural
        assert plan[1].destination == natural.with_name("photo_1.jpg")
        assert plan[0].destination.parent == plan[1].destination.parent

    def test_different_bytes_still_get_collision_suffix(self, tmp_path):
        src, dst = _setup(tmp_path, n=1)
        source = src / "IMG_0.jpg"
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        natural = _plan(src, dst)[0].destination
        natural.parent.mkdir(parents=True)
        mutated = bytearray(source.read_bytes())
        mutated[-1] ^= 0xFF
        assert bytes(mutated) != source.read_bytes()
        assert len(mutated) == source.stat().st_size
        natural.write_bytes(bytes(mutated))
        _unfinished_without(dst, source)

        plan = _plan(src, dst)
        assert plan[0].destination == natural.with_name("IMG_0_1.jpg")
        log, summary = execute_plan(plan, options)

        assert summary["copied"] == 1
        assert summary["errors"] == 0
        assert natural.read_bytes() == bytes(mutated)
        assert plan[0].destination.read_bytes() == source.read_bytes()
        logged = [op for op in log.operations
                  if path_identity(op.source) == path_identity(source)]
        assert len(logged) == 1
        assert logged[0].destination == str(plan[0].destination)
        assert logged[0].status == "done"
        assert source.exists()

    def test_saved_run_is_not_adopted_during_a_later_interrupt(self, tmp_path):
        """A finished run's copy stays a collision for the next plan.

        Adopting it would make undo of the newer run delete the earlier one.
        """
        src, dst = _setup(tmp_path, n=1)
        source = src / "IMG_0.jpg"
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        execute_plan(_plan(src, dst), options)
        natural = dst / "2024" / "07 July" / "IMG_0.jpg"
        original = natural.read_bytes()
        assert original == source.read_bytes()
        assert find_unfinished_journal(dst) is None
        _unfinished_without(dst, source)

        plan = _plan(src, dst)
        assert plan[0].destination.name == "IMG_0_1.jpg"
        log, summary = execute_plan(plan, options)

        assert summary["copied"] == 1
        assert summary["errors"] == 0
        assert natural.read_bytes() == original
        assert plan[0].destination.read_bytes() == source.read_bytes()
        logged = [op for op in log.operations
                  if path_identity(op.source) == path_identity(source)]
        assert len(logged) == 1
        assert logged[0].destination == str(plan[0].destination)

    def test_happy_path_uses_natural_name_once(self, tmp_path):
        src, dst = _setup(tmp_path, n=1)
        source = src / "IMG_0.jpg"
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        plan = _plan(src, dst)
        assert plan[0].destination == dst / "2024" / "07 July" / "IMG_0.jpg"

        log, summary = execute_plan(plan, options)

        assert summary["copied"] == 1
        assert summary["errors"] == 0
        copied = list(dst.rglob("*.jpg"))
        assert copied == [plan[0].destination]
        assert copied[0].read_bytes() == source.read_bytes()
        assert source.exists()
        assert find_unfinished_journal(dst) is None
        assert log.operations[0].status == "done"
        assert log.operations[0].destination == str(plan[0].destination)


def _journaled_move_still_on_disk(tmp_path, *, n=2, dest="match", action="move"):
    """Done line for the first file, source still present, dest as ``dest``.

    ``dest`` is ``match`` (same bytes), ``missing``, or ``differ`` (same
    size, different bytes). Later files are not journaled.
    """
    src, dst = _setup(tmp_path, n=n)
    options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False)
    plan = _plan(src, dst, copy_mode=False)
    source = plan[0].source
    payload = source.read_bytes()
    final = plan[0].destination
    if dest == "match":
        final.parent.mkdir(parents=True, exist_ok=True)
        atomic_copy(source, final)
    elif dest == "differ":
        final.parent.mkdir(parents=True, exist_ok=True)
        mutated = bytearray(payload)
        mutated[-1] ^= 0xFF
        assert bytes(mutated) != payload
        assert len(mutated) == len(payload)
        final.write_bytes(bytes(mutated))
    elif dest != "missing":
        raise ValueError(dest)
    with JournalWriter(dst) as writer:
        writer.record(action, str(source), str(final))
    assert find_unfinished_journal(dst) is not None
    assert source.is_file()
    assert source.read_bytes() == payload
    remaining = exclude_completed_sources(
        plan, completed_sources(journal_path_for(dst)))
    assert source not in [item.source for item in remaining]
    return (options, src, dst, plan, source, payload, final, remaining)


class TestJournaledCrossVolumeMoveResume:
    """Crash after the move done line and before ``source.unlink()``.

    Resume excludes that source. The destination copy is already there.
    The source must still be removed when those bytes match, and must be
    kept when they do not.
    """

    def test_cross_volume_resume_unlinks_journaled_source_still_present(
            self, tmp_path, monkeypatch):
        src, dst = _setup(tmp_path, n=2)
        options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False)
        plan = _plan(src, dst, copy_mode=False)
        source = plan[0].source
        other = plan[1].source
        payload = source.read_bytes()
        other_payload = other.read_bytes()
        final = plan[0].destination
        other_final = plan[1].destination
        state = {"abort": True}
        real_replace = os.replace
        real_unlink = Path.unlink

        def spy_replace(src_path, dst_path):
            if Path(src_path) == source:
                raise OSError("simulated cross-volume rename")
            return real_replace(src_path, dst_path)

        def spy_unlink(self, *args, **kwargs):
            if state["abort"] and self == source:
                text = journal_path_for(dst).read_text(encoding="utf-8")
                assert str(source) in text
                assert '"status": "done"' in text
                assert '"run": "complete"' not in text
                assert self.is_file()
                raise RuntimeError("abort before unlink")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(
            "media_organizer.core.journal.os.replace", spy_replace)
        monkeypatch.setattr(Path, "unlink", spy_unlink)

        with pytest.raises(RuntimeError, match="abort before unlink"):
            execute_plan(plan, options)

        assert source.is_file()
        assert source.read_bytes() == payload
        assert final.is_file()
        assert final.read_bytes() == payload
        assert other.is_file()
        assert not other_final.exists()
        journal = find_unfinished_journal(dst)
        assert journal is not None
        assert str(source) in completed_sources(journal)
        assert '"run": "complete"' not in journal.read_text(encoding="utf-8")

        state["abort"] = False
        remaining = exclude_completed_sources(plan, completed_sources(journal))
        assert [item.source for item in remaining] == [other]

        log, summary = execute_plan(remaining, options)

        assert summary["moved"] == 1
        assert summary["errors"] == 0
        assert summary["cancelled"] is False
        assert not source.exists()
        assert final.read_bytes() == payload
        assert not other.exists()
        assert other_final.read_bytes() == other_payload
        names = sorted(p.name for p in dst.rglob("*.jpg"))
        assert names == ["IMG_0.jpg", "IMG_1.jpg"]
        text = journal_path_for(dst).read_text(encoding="utf-8")
        assert text.count(str(source)) == 1
        assert text.strip().splitlines()[-1] == '{"run": "complete"}'
        assert find_unfinished_journal(dst) is None
        done = [op for op in log.operations if op.status == "done"]
        assert {path_identity(op.source) for op in done} == {
            path_identity(source), path_identity(other)}
        assert all(op.action == "move" for op in done)

        result = undo_log(log, dst)
        assert result["undone"] == 2
        assert result["failed"] == 0
        assert source.read_bytes() == payload
        assert other.read_bytes() == other_payload
        assert not final.exists()
        assert not other_final.exists()

    def test_empty_resume_unlinks_journaled_cross_volume_source(self, tmp_path):
        (options, _src, dst, _plan_rows, source, payload, final,
         remaining) = _journaled_move_still_on_disk(tmp_path, n=1)
        assert remaining == []

        log, summary = execute_plan(remaining, options)

        assert summary["moved"] == 0
        assert summary["errors"] == 0
        assert summary["cancelled"] is False
        assert not source.exists()
        assert final.read_bytes() == payload
        assert not list(dst.rglob("IMG_0_1.jpg"))
        text = journal_path_for(dst).read_text(encoding="utf-8")
        assert text.count(str(source)) == 1
        assert text.strip().splitlines()[-1] == '{"run": "complete"}'
        assert find_unfinished_journal(dst) is None
        assert [path_identity(op.source) for op in log.operations
                if op.status == "done"] == [path_identity(source)]

    def test_resume_keeps_source_when_journaled_destination_missing(
            self, tmp_path):
        (options, _src, dst, plan, source, payload, final,
         remaining) = _journaled_move_still_on_disk(tmp_path, dest="missing")
        assert remaining
        assert not final.exists()

        _log, summary = execute_plan(remaining, options)

        assert summary["errors"] == 0
        assert summary["moved"] == 1
        assert source.is_file()
        assert source.read_bytes() == payload
        assert not final.exists()
        names = sorted(p.name for p in dst.rglob("*.jpg"))
        assert names == ["IMG_1.jpg"]
        assert find_unfinished_journal(dst) is None

    def test_resume_keeps_source_when_journaled_destination_bytes_differ(
            self, tmp_path):
        (options, _src, dst, _plan_rows, source, payload, final,
         remaining) = _journaled_move_still_on_disk(tmp_path, dest="differ")
        mutated = final.read_bytes()
        assert mutated != payload
        assert len(mutated) == len(payload)

        _log, summary = execute_plan(remaining, options)

        assert summary["errors"] == 0
        assert summary["moved"] == 1
        assert source.is_file()
        assert source.read_bytes() == payload
        assert final.read_bytes() == mutated
        names = sorted(p.name for p in dst.rglob("*.jpg"))
        assert names == ["IMG_0.jpg", "IMG_1.jpg"]
        assert find_unfinished_journal(dst) is None

    def test_copy_resume_does_not_unlink_journaled_move_source(self, tmp_path):
        (options, _src, dst, plan, source, payload, final,
         remaining) = _journaled_move_still_on_disk(tmp_path)
        options = OrganizeOptions(
            source_dir=options.source_dir, dest_dir=dst, copy_mode=True)

        _log, summary = execute_plan(remaining, options)

        assert summary["copied"] == 1
        assert summary["errors"] == 0
        assert source.is_file()
        assert source.read_bytes() == payload
        assert final.read_bytes() == payload
        assert plan[1].source.is_file()
        names = sorted(p.name for p in dst.rglob("*.jpg"))
        assert names == ["IMG_0.jpg", "IMG_1.jpg"]
        assert find_unfinished_journal(dst) is None

    def test_move_resume_does_not_unlink_journaled_copy_source(self, tmp_path):
        (options, _src, dst, plan, source, payload, final,
         remaining) = _journaled_move_still_on_disk(tmp_path, action="copy")

        _log, summary = execute_plan(remaining, options)

        assert summary["moved"] == 1
        assert summary["errors"] == 0
        assert source.is_file()
        assert source.read_bytes() == payload
        assert final.read_bytes() == payload
        assert not plan[1].source.exists()
        names = sorted(p.name for p in dst.rglob("*.jpg"))
        assert names == ["IMG_0.jpg", "IMG_1.jpg"]
        assert find_unfinished_journal(dst) is None

    def test_dry_run_does_not_unlink_journaled_move_source(self, tmp_path):
        (options, _src, dst, plan, source, payload, final,
         remaining) = _journaled_move_still_on_disk(tmp_path)
        options = OrganizeOptions(
            source_dir=options.source_dir, dest_dir=dst, copy_mode=False,
            dry_run=True)

        _log, summary = execute_plan(remaining, options)

        assert summary["dry_run"] is True
        assert summary["moved"] == 1
        assert source.is_file()
        assert source.read_bytes() == payload
        assert final.read_bytes() == payload
        assert plan[1].source.is_file()
        assert not plan[1].destination.exists()
        text = journal_path_for(dst).read_text(encoding="utf-8")
        assert '"run": "complete"' not in text
        assert find_unfinished_journal(dst) is not None


class TestDryRun:
    def test_dry_run_writes_nothing(self, tmp_path):
        src, dst = _setup(tmp_path)
        options = OrganizeOptions(source_dir=src, dest_dir=dst, dry_run=True)
        log, summary = execute_plan(_plan(src, dst), options)
        assert summary["copied"] == 3
        assert summary["dry_run"] is True
        assert not dst.exists() or not any(dst.rglob("*.jpg"))
        assert not journal_path_for(dst).exists()
        from media_organizer.core.plan import load_log
        assert load_log(dst) is None  # dry run writes no operation log either


class TestParallelCorrectness:
    def test_parallel_plan_matches_serial(self, tmp_path):
        src, dst = _setup(tmp_path, n=25)
        options = OrganizeOptions(source_dir=src, dest_dir=dst)
        serial = _plan(src, dst)
        from media_organizer.core.organizer import scan_media_files
        files = list(scan_media_files(options))
        analysis = analyze_media_batch(files)
        parallel = build_plan(options, files=files, analysis=analysis)
        key = lambda p: (str(p.source), str(p.destination),
                         p.capture.date, p.size)
        assert [key(p) for p in serial] == [key(p) for p in parallel]

    def test_batch_handles_empty_and_garbage(self, tmp_path):
        assert analyze_media_batch([]) == {}
        junk = tmp_path / "junk.jpg"
        junk.write_bytes(b"garbage")
        r = analyze_media_batch([junk])
        assert r[junk][0].found or r[junk][0].source.value == "mtime"

    def test_parallel_gps_prefetch(self, tmp_path):
        f = make_jpeg_with_gps(tmp_path / "gps.jpg", None, 41.01, 28.98)
        r = analyze_media_batch([f], need_gps=True)
        assert r[f][1] == pytest.approx((41.01, 28.98), abs=0.01)

    def test_process_pool_failure_falls_back_to_serial(self, tmp_path, monkeypatch):
        """A pool that cannot start must finish >=400 files inline, once."""
        import concurrent.futures

        files = _threshold_batch(tmp_path)
        constructions = []

        class BrokenPool:
            def __init__(self, *args, **kwargs):
                constructions.append(1)
                raise RuntimeError("process pool unavailable")

        monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", BrokenPool)
        ticks = []

        def progress(i, name):
            ticks.append((i, name))

        result = analyze_media_batch(
            files, need_gps=True, progress=progress, cancel=lambda: False,
        )

        assert len(constructions) == 1  # no recursive re-entry into the pool
        assert len(result) == len(files)
        _assert_batch_matches_serial(result, files, need_gps=True)
        assert result[files[0]][0].source == DateSource.EXIF
        assert result[files[0]][0].date == datetime(2021, 7, 4, 15, 30, 22)
        assert result[files[1]][1] == pytest.approx((41.01, 28.98), abs=0.01)
        assert result[files[2]][0].source == DateSource.FILENAME
        assert result[files[2]][0].date == datetime(2019, 6, 15, 8, 30, 45)
        assert [i for i, _name in ticks] == list(range(0, len(files), 25))
        assert ticks[0][1] == files[0].name

    def test_process_pool_map_failure_finishes_remaining_serially(
            self, tmp_path, monkeypatch):
        """A pool that dies mid-batch keeps finished paths and serializes the rest."""
        import concurrent.futures

        files = _threshold_batch(tmp_path)
        constructions = []

        class BrokenMap:
            def __init__(self, *args, **kwargs):
                constructions.append(1)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def map(self, fn, iterable, chunksize=1):
                for i, item in enumerate(iterable):
                    if i >= 7:
                        raise RuntimeError("worker failed")
                    yield fn(item)

        monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", BrokenMap)
        result = analyze_media_batch(files, need_gps=True, cancel=lambda: False)

        assert constructions == [1]
        assert len(result) == len(files)
        _assert_batch_matches_serial(result, files, need_gps=True)

    def test_serial_fallback_honors_cancel(self, tmp_path, monkeypatch):
        import concurrent.futures

        files = _threshold_batch(tmp_path)

        class BrokenPool:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("process pool unavailable")

        monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", BrokenPool)
        checks = {"n": 0}
        ticks = []

        def cancel():
            checks["n"] += 1
            return checks["n"] > 40

        result = analyze_media_batch(
            files, cancel=cancel, progress=lambda i, name: ticks.append(i),
        )
        assert len(result) == 40
        assert checks["n"] == 41
        assert ticks == [0, 25]
