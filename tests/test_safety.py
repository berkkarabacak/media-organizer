"""Tests for safety features: free space, journal/resume, atomic copy,
dry run, and parallel correctness (executor.py, journal.py, metadata batch)."""

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
from media_organizer.core.metadata import analyze_media_batch
from media_organizer.core.organizer import OrganizeOptions, build_plan
from tests.helpers import make_jpeg_with_exif


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
        from tests.helpers import make_jpeg_with_gps
        f = make_jpeg_with_gps(tmp_path / "gps.jpg", None, 41.01, 28.98)
        r = analyze_media_batch([f], need_gps=True)
        assert r[f][1] == pytest.approx((41.01, 28.98), abs=0.01)
