"""Tests for display helpers (display.py)."""

from pathlib import Path

from media_organizer.core.display import elide_middle, relative_destination


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
