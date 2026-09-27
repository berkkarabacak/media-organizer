"""Tests for display helpers (display.py)."""

from datetime import datetime
from pathlib import Path

from media_organizer.core.display import (
    elide_middle, format_bytes, plan_sort_key, relative_destination,
    sorted_plan_items,
)
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
