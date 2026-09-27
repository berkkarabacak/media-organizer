"""Tests for the _uncertain (mtime-guess) handling option."""

import os
from datetime import datetime

from media_organizer.core.metadata import DateSource
from media_organizer.core.organizer import OrganizeOptions, build_plan
from media_organizer.core.strategies import UNCERTAIN_FOLDER
from tests.helpers import make_jpeg_with_exif


def _mtime_only(path, tmp_path, ts=datetime(2019, 3, 2, 10, 0, 0).timestamp()):
    """Corrupt JPEG with no date in the name -> only mtime is available."""
    f = tmp_path / "src" / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 20)
    os.utime(f, (ts, ts))
    return f


def _mixed(tmp_path):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    make_jpeg_with_exif(src / "good.jpg", datetime(2024, 7, 15, 10, 0, 0))
    _mtime_only("guessed.jpg", tmp_path)
    return src, tmp_path / "dst"


class TestUncertainOption:
    def test_default_is_aside(self):
        assert OrganizeOptions(source_dir="s", dest_dir="d").uncertain == "aside"

    def test_aside_routes_mtime_guesses_to_uncertain(self, tmp_path):
        src, dst = _mixed(tmp_path)
        plan = build_plan(OrganizeOptions(source_dir=src, dest_dir=dst))
        by_name = {p.source.name: p for p in plan}
        assert by_name["guessed.jpg"].capture.source is DateSource.MTIME
        assert by_name["guessed.jpg"].destination == \
            dst / UNCERTAIN_FOLDER / "guessed.jpg"
        # EXIF file unaffected
        assert by_name["good.jpg"].destination == \
            dst / "2024" / "07 July" / "good.jpg"

    def test_use_routes_mtime_guesses_to_date_folders(self, tmp_path):
        src, dst = _mixed(tmp_path)
        plan = build_plan(OrganizeOptions(source_dir=src, dest_dir=dst,
                                          uncertain="use"))
        by_name = {p.source.name: p for p in plan}
        assert by_name["guessed.jpg"].destination == \
            dst / "2019" / "03 March" / "guessed.jpg"

    def test_aside_under_location_strategy(self, tmp_path):
        src, dst = _mixed(tmp_path)
        plan = build_plan(OrganizeOptions(source_dir=src, dest_dir=dst,
                                          strategy="location"))
        by_name = {p.source.name: p for p in plan}
        assert by_name["guessed.jpg"].destination == \
            dst / UNCERTAIN_FOLDER / "guessed.jpg"
        # no-GPS EXIF file still falls to _unknown-location
        assert by_name["good.jpg"].destination == \
            dst / "_unknown-location" / "good.jpg"

    def test_high_confidence_sources_never_aside(self, tmp_path):
        src, dst = _mixed(tmp_path)
        plan = build_plan(OrganizeOptions(source_dir=src, dest_dir=dst))
        good = next(p for p in plan if p.source.name == "good.jpg")
        assert good.capture.source is DateSource.EXIF
        assert UNCERTAIN_FOLDER not in str(good.destination)
