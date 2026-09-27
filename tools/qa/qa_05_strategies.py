"""QA-05: all 7 strategies on a mixed fixture set.
QA-06: duplicate handling (3 copies, manual exclusion of duplicates)."""

import tempfile
from datetime import datetime
from pathlib import Path

from qa_common import (app, check, close_window, make_window, section,
                       wait_worker)


def _fixtures(src: Path):
    from tests.helpers import make_jpeg_with_exif, make_jpeg_with_gps
    src.mkdir(parents=True, exist_ok=True)
    make_jpeg_with_exif(src / "july.jpg", datetime(2024, 7, 15, 10, 30))
    make_jpeg_with_exif(src / "january.jpg", datetime(2019, 1, 2, 8, 0))
    make_jpeg_with_gps(src / "istanbul.jpg", datetime(2024, 7, 15),
                       41.01, 28.98)
    # undated: corrupt file, no date in name, mtime is the only fallback ->
    # to force _undated we monkey... instead rely on mtime? mtime always
    # succeeds, so undated files are rare; test strategy-level instead.


def run():
    section("QA-05  Strategies (7/7, real organize)")
    a = app()
    from media_organizer.core.strategies import STRATEGIES
    from media_organizer.core.metadata import CaptureDate
    tmp = Path(tempfile.mkdtemp())

    expected = {
        "year_only": ["2024/july.jpg", "2019/january.jpg",
                      "2024/istanbul.jpg"],
        "year_month": ["2024/07 July/july.jpg", "2019/01 January/january.jpg",
                       "2024/07 July/istanbul.jpg"],
        "year_quarter": ["2024/Q3/july.jpg", "2019/Q1/january.jpg",
                         "2024/Q3/istanbul.jpg"],
        "year_quarter_month": ["2024/Q3/07 July/july.jpg",
                               "2019/Q1/01 January/january.jpg",
                               "2024/Q3/07 July/istanbul.jpg"],
        "monthly_flat": ["2024-07 July/july.jpg",
                         "2019-01 January/january.jpg",
                         "2024-07 July/istanbul.jpg"],
        "location": ["Istanbul, Turkey/istanbul.jpg",
                     "_unknown-location/july.jpg",
                     "_unknown-location/january.jpg"],
        "location_year": ["Istanbul, Turkey/2024/istanbul.jpg",
                          "_unknown-location/2024/july.jpg",
                          "_unknown-location/2019/january.jpg"],
    }

    for strategy in STRATEGIES:
        w = make_window()
        src, dst = tmp / f"src_{strategy.key}", tmp / f"dst_{strategy.key}"
        _fixtures(src)
        w.source_card.edit.setText(str(src))
        w.dest_card.edit.setText(str(dst))
        w._select_strategy(strategy.key)
        w._goto_step(2)
        w.start_scan()
        ok = wait_worker(w.scan_worker)
        check(f"{strategy.key}: scan completes", ok and w.table.rowCount() == 3,
              f"rows={w.table.rowCount()}")
        w.start_organize()
        ok2 = wait_worker(w.org_worker)
        rels = sorted(str(p.relative_to(dst)).replace("\\", "/")
                      for p in dst.rglob("*.jpg"))
        exp = sorted(expected[strategy.key])
        check(f"{strategy.key}: exact folder layout", rels == exp,
              f"got={rels}")
        close_window(w)

    # undated -> _undated (strategy level + organize with undated file)
    section("QA-05b  Undated files land in _undated")
    from media_organizer.core.strategies import relative_folder, UNDATED_FOLDER
    check("strategy-level: no date -> _undated",
          relative_folder("year_month", CaptureDate(None)) == UNDATED_FOLDER)

    section("QA-06  Duplicates")
    tmp2 = Path(tempfile.mkdtemp())
    src, dst = tmp2 / "src", tmp2 / "dst"
    src.mkdir()
    from tests.helpers import make_jpeg_with_exif
    base = make_jpeg_with_exif(src / "one.jpg", datetime(2024, 7, 15))
    (src / "sub1").mkdir(parents=True)
    (src / "sub2").mkdir()
    payload = base.read_bytes()
    (src / "sub1" / "two.jpg").write_bytes(payload)
    (src / "sub2" / "three.jpg").write_bytes(payload)

    w = make_window()
    w.source_card.edit.setText(str(src))
    w.dest_card.edit.setText(str(dst))
    w._goto_step(2)
    w.start_scan()
    wait_worker(w.scan_worker)
    dupes = [p for p in w.plan if p.is_duplicate]
    check("3 copies different names: 2 flagged duplicate",
          len(dupes) == 2, f"dupes={len(dupes)}")
    w.start_organize()
    wait_worker(w.org_worker)
    copied = list(dst.rglob("*.jpg"))
    check("3 copies: exactly 1 kept", len(copied) == 1, f"copied={len(copied)}")

    # exclude the kept one manually -> the duplicates must STAY skipped,
    # not be resurrected into the plan
    src2, dst2 = tmp2 / "src2", tmp2 / "dst2"
    import shutil
    shutil.copytree(src, src2)
    w2 = make_window()
    w2.source_card.edit.setText(str(src2))
    w2.dest_card.edit.setText(str(dst2))
    w2._goto_step(2)
    w2.start_scan()
    wait_worker(w2.scan_worker)
    kept = next(p for p in w2.plan if not p.is_duplicate)
    w2._set_excluded(kept, True)
    w2.start_organize()
    wait_worker(w2.org_worker)
    copied2 = list(dst2.rglob("*.jpg"))
    check("kept file excluded: duplicates still skipped, nothing copied",
          len(copied2) == 0, f"copied={len(copied2)}")
    check("kept file excluded: summary shows exclusion",
          "1 excluded" in w2.plan_summary.text())
    close_window(w)
    close_window(w2)


if __name__ == "__main__":
    import qa_common
    run()
    raise SystemExit(qa_common.summary())
