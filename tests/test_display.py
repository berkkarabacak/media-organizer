"""Tests for display helpers (display.py)."""

from datetime import datetime
from pathlib import Path

from media_organizer.core.display import (
    elide_middle, finished_run_keeps_plan, finished_run_lines, format_bytes,
    plan_sort_key, relative_destination, sorted_plan_items,
)
from media_organizer.core.plan import UNDO_STACK_LIMIT
from media_organizer.core.metadata import CaptureDate, DateSource


class TestFormatBytes:
    def test_units(self):
        assert format_bytes(0) == "0 B"
        assert format_bytes(512) == "512 B"
        assert format_bytes(1023) == "1023 B"
        assert format_bytes(1024) == "1.0 KB"
        assert format_bytes(812_455) == "793.4 KB"
        assert format_bytes(4_238_456) == "4.0 MB"
        assert format_bytes(48.2 * 1024**3) == "48.2 GB"
        assert format_bytes(2 * 1024**4) == "2.0 TB"

    def test_negative_clamped(self):
        assert format_bytes(-5) == "0 B"


class _FakeItem:
    """Duck-typed PlannedFile for sort-key tests."""

    def __init__(self, name, size, dt=None, dest=None):
        self.source = Path(f"/src/{name}")
        self.size = size
        self.capture = CaptureDate(dt, DateSource.EXIF) if dt else CaptureDate(None)
        self.destination = Path(dest) if dest else None


def _rows():
    return [
        _FakeItem("b.jpg", 300, datetime(2024, 7, 1), "/dst/b.jpg"),
        _FakeItem("A.jpg", 100, datetime(2023, 1, 1), "/dst/a.jpg"),
        _FakeItem("c.jpg", 200, None, None),          # no date, no dest
    ]


class TestPlanSortKeys:
    def test_file_case_insensitive(self):
        ordered = sorted_plan_items(_rows(), "file")
        assert [r.source.name for r in ordered] == ["A.jpg", "b.jpg", "c.jpg"]

    def test_size_numeric(self):
        ordered = sorted_plan_items(_rows(), "size")
        assert [r.size for r in ordered] == [100, 200, 300]
        desc = sorted_plan_items(_rows(), "size", descending=True)
        assert [r.size for r in desc] == [300, 200, 100]

    def test_date_chronological(self):
        ordered = sorted_plan_items(_rows(), "date")
        assert [r.capture.date for r in ordered[:2]] == [
            datetime(2023, 1, 1), datetime(2024, 7, 1)]

    def test_missing_values_always_last(self):
        for desc in (False, True):
            for col in ("date", "dest"):
                ordered = sorted_plan_items(_rows(), col, descending=desc)
                assert ordered[-1].source.name == "c.jpg"

    def test_plan_not_mutated(self):
        rows = _rows()
        sorted_plan_items(rows, "size")
        assert [r.size for r in rows] == [300, 100, 200]

    def test_dest_and_source_keys(self):
        assert plan_sort_key("dest", _rows()[0]) == \
            str(Path("/dst/b.jpg")).lower()
        assert plan_sort_key("source", _rows()[0]) == "exif"

    def test_relative_destination_fast_preserves_case(self):
        # regression: normcase lowercased the DISPLAYED path too
        import os
        from media_organizer.core.display import relative_destination_fast
        root = os.path.normcase(os.path.normpath("C:/Photos_Organized"))
        shown = relative_destination_fast(
            "C:/Photos_Organized/2024/07 July/IMG_1234.jpg", root)
        assert shown == "2024/07 July/IMG_1234.jpg"

    def test_relative_destination_fast_outside_root(self):
        import os
        from media_organizer.core.display import relative_destination_fast
        root = os.path.normcase(os.path.normpath("C:/Photos_Organized"))
        shown = relative_destination_fast("D:/elsewhere/f.jpg", root)
        assert shown == "D:/elsewhere/f.jpg"


class TestRelativeDestination:
    def test_relative_path_shown(self, tmp_path):
        dest_root = tmp_path / "Organized"
        dest = dest_root / "2024" / "Q3" / "07 July" / "IMG_1234.jpg"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"x")
        assert relative_destination(dest, dest_root) == \
            "2024/Q3/07 July/IMG_1234.jpg"

    def test_not_under_root_falls_back_to_absolute(self, tmp_path):
        dest = tmp_path / "elsewhere" / "file.jpg"
        dest.parent.mkdir()
        dest.write_bytes(b"x")
        shown = relative_destination(dest, tmp_path / "Organized")
        assert shown == str(dest)

    def test_none_destination(self):
        assert relative_destination(None, "C:/whatever") == "—"

    def test_no_root_shows_absolute(self, tmp_path):
        dest = tmp_path / "f.jpg"
        assert relative_destination(dest, None) == str(dest)

    def test_paths_that_dont_exist_yet(self):
        # plan-time destinations don't exist on disk; still relative
        shown = relative_destination(
            Path("C:/Photos_Organized/2024/07 July/a.jpg"),
            "C:/Photos_Organized")
        assert shown == "2024/07 July/a.jpg"


class TestElideMiddle:
    def test_short_text_unchanged(self):
        assert elide_middle("2024/07 July/a.jpg") == "2024/07 July/a.jpg"

    def test_long_path_keeps_head_and_tail(self):
        text = "C:/" + "deep/" * 40 + "IMG_1234.jpg"
        out = elide_middle(text, 60)
        assert len(out) <= 60
        assert out.startswith("C:/")
        assert out.endswith("IMG_1234.jpg")
        assert "..." in out

    def test_never_bare_drive_prefix(self):
        text = "C:/" + "x" * 200 + "/photo.jpg"
        out = elide_middle(text, 40)
        assert out != "C:\\..." and out != "C:/..."
        assert "photo.jpg" in out


class TestFinishedRunUndoWording:
    def test_real_run_names_the_undo_stack(self):
        _status, text = finished_run_lines(
            {"copied": 2, "moved": 0, "action": "copy", "dry_run": False},
            folders=1, total_bytes=10)
        assert f"The last {UNDO_STACK_LIMIT} organize runs" in text
        assert "newest first" in text
        assert "replaces" not in text.lower()
        assert "cannot be undone" not in text.lower()

    def test_dry_run_does_not_mention_undo(self):
        _status, text = finished_run_lines(
            {"copied": 2, "moved": 0, "action": "copy", "dry_run": True},
            folders=1, total_bytes=10)
        assert "undo" not in text.lower()


def _run_summary(**overrides):
    summary = {
        "copied": 1,
        "moved": 0,
        "skipped_duplicates": 0,
        "undated": 0,
        "uncertain": 0,
        "errors": 0,
        "cancelled": False,
        "dry_run": False,
        "action": "copy",
    }
    summary.update(overrides)
    return summary


class TestPartialFinishKeepsResume:
    """Cancel and per-file errors stay resumable. A clean finish does not."""

    def test_open_journal_keeps_the_plan_for_cancel_and_errors(self):
        assert finished_run_keeps_plan(
            _run_summary(cancelled=True), journal_open=True)
        assert finished_run_keeps_plan(
            _run_summary(errors=1, copied=1), journal_open=True)
        # The journal is the signal. A summary that looks clean still
        # stays armed when complete() never landed.
        assert finished_run_keeps_plan(
            _run_summary(copied=2), journal_open=True)

    def test_clean_finish_and_dry_run_drop_the_plan(self):
        assert not finished_run_keeps_plan(
            _run_summary(copied=2), journal_open=False)
        assert not finished_run_keeps_plan(
            _run_summary(dry_run=True, copied=2), journal_open=True)
        assert not finished_run_keeps_plan(
            _run_summary(dry_run=True, cancelled=True, errors=1),
            journal_open=True)

    def test_errors_are_not_called_permanently_skipped(self):
        status, text = finished_run_lines(
            _run_summary(errors=1, copied=1),
            folders=1, total_bytes=10, journal_open=True)
        assert "couldn't be read" not in text
        assert "skipped" not in text.lower()
        assert "1 file could not be organized" in text
        assert "Organize again to Resume" in text
        assert "Organize again to Resume" in status
        assert "Done!" not in text
        assert not status.startswith("Finished")
        assert "Undo reverses" in text
        assert f"The last {UNDO_STACK_LIMIT} organize runs" in text

    def test_several_errors_use_the_plural(self):
        _status, text = finished_run_lines(
            _run_summary(errors=2, copied=1),
            folders=1, total_bytes=10, journal_open=True)
        assert "2 files could not be organized" in text
        assert "couldn't be read" not in text

    def test_cancel_points_at_resume(self):
        status, text = finished_run_lines(
            _run_summary(cancelled=True, copied=1),
            folders=1, total_bytes=10, journal_open=True)
        assert "skipped" not in text.lower()
        assert "couldn't be read" not in text
        assert "cancelled part-way" in text
        assert "Organize again to Resume" in text
        assert "Organize again to Resume" in status
        assert "Done!" not in text
        assert not status.startswith("Finished")

    def test_stopped_move_says_moved(self):
        status, text = finished_run_lines(
            _run_summary(action="move", moved=1, copied=0, cancelled=True),
            folders=1, total_bytes=10, journal_open=True)
        assert "1 moved" in status
        assert "moved" in text
        assert "copied" not in status
        assert "copied" not in text

    def test_closed_journal_still_says_done(self):
        status, text = finished_run_lines(
            _run_summary(copied=2, errors=0),
            folders=1, total_bytes=10, journal_open=False)
        assert status == "Finished: 2 copied."
        assert text.startswith("Done!")
        assert "Resume" not in text
        assert "could not be organized" not in text

    def test_errors_without_a_journal_are_not_called_skipped(self):
        _status, text = finished_run_lines(
            _run_summary(errors=1, dry_run=False),
            folders=1, total_bytes=10, journal_open=False)
        assert "couldn't be read" not in text
        assert "skipped" not in text.lower()
        assert "1 file could not be organized" in text
        assert "Resume" not in text

    def test_dry_run_with_an_open_journal_does_not_say_resume(self):
        status, text = finished_run_lines(
            _run_summary(dry_run=True, errors=1, cancelled=True),
            folders=1, total_bytes=10, journal_open=True)
        assert "Resume" not in text
        assert "Resume" not in status
        assert "undo" not in text.lower()
        assert "couldn't be read" not in text
        assert "skipped" not in text.lower()
        assert "Dry run" in text
