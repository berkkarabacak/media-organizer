"""End-to-end core tests: build_plan + execute_plan + undo (no Qt)."""

import os
from datetime import datetime
from pathlib import Path

from media_organizer.core.executor import execute_plan
from media_organizer.core.organizer import (OrganizeOptions, build_plan,
                                            count_media_files)
from media_organizer.core.plan import load_log, undo_log
from tests.helpers import make_jpeg_with_exif, make_jpeg_with_gps


def _options(src: Path, dst: Path, **kw) -> OrganizeOptions:
    return OrganizeOptions(source_dir=src, dest_dir=dst, **kw)


def _photo(path: Path, dt=datetime(2024, 7, 15, 10, 30, 0)) -> Path:
    return make_jpeg_with_exif(path, dt)


class TestBuildPlanStrategies:
    def test_default_year_month(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        f = _photo(src / "IMG_1.jpg")
        plan = build_plan(_options(src, dst))
        assert plan[0].destination == dst / "2024" / "07 July" / "IMG_1.jpg"

    def test_flat_monthly(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        _photo(src / "IMG_1.jpg")
        plan = build_plan(_options(src, dst, strategy="monthly_flat"))
        assert plan[0].destination.parent.name == "2024-07 July"
        assert plan[0].destination.parent.parent == dst

    def test_quarter_nested(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        _photo(src / "IMG_1.jpg")
        plan = build_plan(_options(src, dst, strategy="year_quarter_month"))
        assert plan[0].destination == \
            dst / "2024" / "Q3" / "07 July" / "IMG_1.jpg"

    def test_location_strategy_with_gps(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        make_jpeg_with_gps(src / "photo.jpg", datetime(2024, 7, 15),
                           41.01, 28.98)
        plan = build_plan(_options(src, dst, strategy="location"))
        assert plan[0].location == "Istanbul, Turkey"
        assert plan[0].destination.parent.name == "Istanbul, Turkey"

    def test_location_strategy_without_gps(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        _photo(src / "IMG_1.jpg")  # EXIF date but no GPS
        plan = build_plan(_options(src, dst, strategy="location"))
        assert plan[0].destination.parent.name == "_unknown-location"

    def test_mtime_fallback_set_aside_by_default(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        f = src / "nodate.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 20)  # corrupt, no metadata
        ts = datetime(2019, 3, 2, 10, 0, 0).timestamp()
        os.utime(f, (ts, ts))
        plan = build_plan(_options(src, dst))
        assert len(plan) == 1
        assert plan[0].capture.found  # mtime fallback
        assert plan[0].destination.parent.name == "_uncertain"

    def test_mtime_fallback_used_when_opted_in(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        f = src / "nodate.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 20)
        ts = datetime(2019, 3, 2, 10, 0, 0).timestamp()
        os.utime(f, (ts, ts))
        plan = build_plan(_options(src, dst, uncertain="use"))
        assert plan[0].destination.parent.name == "03 March"

    def test_never_overwrites_collision_suffixes(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        d = dst / "2024" / "07 July"
        d.mkdir(parents=True)
        (d / "IMG_1.jpg").write_bytes(b"existing")
        _photo(src / "IMG_1.jpg")
        plan = build_plan(_options(src, dst))
        assert plan[0].destination.name == "IMG_1_1.jpg"

    def test_same_name_from_two_subfolders_gets_suffix(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        (src / "a").mkdir(parents=True)
        (src / "b").mkdir()
        _photo(src / "a" / "IMG_1.jpg")
        _photo(src / "b" / "IMG_1.jpg")
        plan = build_plan(_options(src, dst))
        names = {p.destination.name for p in plan}
        assert names == {"IMG_1.jpg", "IMG_1_1.jpg"}

    def test_destination_tree_is_never_scanned(self, tmp_path):
        src = tmp_path / "src"
        dst = src / "organized"  # inside the source tree
        src.mkdir()
        dst.mkdir()
        _photo(dst / "already_there.jpg")
        plan = build_plan(_options(src, dst))
        assert plan == []


class TestPreCount:
    def test_count_media_files(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        _photo(src / "a.jpg")
        _photo(src / "b.jpg")
        (src / "notes.txt").write_text("not media")
        (src / "sub").mkdir()
        _photo(src / "sub" / "c.jpg")
        options = _options(src, tmp_path / "dst")
        assert count_media_files(options) == 3

    def test_count_respects_nonrecursive(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        _photo(src / "a.jpg")
        (src / "sub").mkdir()
        _photo(src / "sub" / "b.jpg")
        options = _options(src, tmp_path / "dst", recursive=False)
        assert count_media_files(options) == 1


class TestExecutePlan:
    def test_copy_and_byte_progress(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        _photo(src / "IMG_1.jpg")
        _photo(src / "IMG_2.jpg", datetime(2024, 1, 5))
        options = _options(src, dst)
        plan = build_plan(options)
        events = []
        log, summary = execute_plan(
            plan, options,
            progress=lambda i, t, n, bd, tb: events.append((i, t, bd, tb)))
        assert summary["copied"] == 2
        assert summary["errors"] == 0
        # originals stay (copy mode)
        assert (src / "IMG_1.jpg").exists()
        assert (dst / "2024" / "07 July" / "IMG_1.jpg").exists()
        # final progress event reports all bytes done
        assert events[-1][0] == events[-1][1] == 2
        assert events[-1][2] == events[-1][3] > 0

    def test_duplicates_skipped_and_logged(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        a = _photo(src / "first.jpg")
        b = src / "renamed.jpg"
        b.write_bytes(a.read_bytes())  # same bytes, different name
        options = _options(src, dst, skip_duplicates=True)
        from media_organizer.core.duplicates import find_duplicates
        dupes = find_duplicates(list(src.iterdir()))
        plan = build_plan(options, duplicates=dupes)
        assert sum(1 for p in plan if p.is_duplicate) == 1
        _log, summary = execute_plan(plan, options)
        assert summary["copied"] == 1
        assert summary["skipped_duplicates"] == 1

    def test_undo_roundtrip(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        _photo(src / "IMG_1.jpg")
        options = _options(src, dst)
        plan = build_plan(options)
        execute_plan(plan, options)
        copied = dst / "2024" / "07 July" / "IMG_1.jpg"
        assert copied.exists()
        log = load_log(dst)
        assert log is not None and not log.undone
        result = undo_log(log, dst)
        assert result["undone"] == 1
        assert not copied.exists()
        assert (src / "IMG_1.jpg").exists()  # original untouched

    def test_move_mode_removes_source(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        _photo(src / "IMG_1.jpg")
        options = _options(src, dst, copy_mode=False)
        plan = build_plan(options)
        _log, summary = execute_plan(plan, options)
        assert summary["moved"] == 1
        assert not (src / "IMG_1.jpg").exists()
