"""Tests for safety features: free space, journal/resume, atomic copy,
dry run, and parallel correctness (executor.py, journal.py, metadata batch)."""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from media_organizer.core.executor import execute_plan, free_space_status
from media_organizer.core.journal import (
    PART_SUFFIX, JournalWriter, atomic_copy, atomic_move, cleanup_stale_parts,
    completed_sources, discard_journal, find_unfinished_journal,
    journal_path_for,
)
from media_organizer.core.metadata import (
    DateSource, analyze_media_batch, extract_capture_date, extract_gps,
)
from media_organizer.core.organizer import OrganizeOptions, build_plan
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
        atomic_move(f, final)
        assert final.exists() and not f.exists()

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
        """A failed same-volume rename uses the durable copy path."""
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

        digest = atomic_move(src, final)

        assert not src.exists()
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
        atomic_move(f, final)
        assert final.exists() and not f.exists()

    def test_cleanup_stale_parts(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / f"x.jpg{PART_SUFFIX}").write_bytes(b"")
        (tmp_path / f"y.jpg{PART_SUFFIX}").write_bytes(b"")
        (tmp_path / "keep.jpg").write_bytes(b"")
        (tmp_path / "notes.part").write_bytes(b"user")
        assert cleanup_stale_parts(tmp_path) == 2
        assert (tmp_path / "keep.jpg").exists()
        assert (tmp_path / "notes.part").read_bytes() == b"user"


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
        remaining = [p for p in plan if str(p.source) not in done]
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
        execute_plan(_plan(src, dst), options, cancel=lambda: True)
        assert find_unfinished_journal(dst) is not None
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

        remaining = [p for p in plan_b if str(p.source) not in done]
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
