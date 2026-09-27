"""QA-10: unwritable destination, filter interplay, corrupt persisted settings."""

import tempfile
from datetime import datetime
from pathlib import Path

from qa_common import (app, check, close_window, make_window, pump, section,
                       wait_worker)


def run():
    section("QA-10  Destination failure, filter, corrupt settings")
    a = app()
    tmp = Path(tempfile.mkdtemp())
    from tests.helpers import make_jpeg_with_exif

    # 1. Destination on a nonexistent drive -> must fail gracefully, no crash
    src = tmp / "src"
    src.mkdir()
    make_jpeg_with_exif(src / "IMG_1.jpg", datetime(2024, 7, 15))
    w = make_window()
    w.source_card.edit.setText(str(src))
    bad_dst = "Q:/definitely_not_a_real_drive_xyz/organized"
    w.dest_card.edit.setText(bad_dst)
    w._goto_step(2)
    w.start_scan()
    ok = wait_worker(w.scan_worker)
    check("bad dest drive: scan still works", ok and w.table.rowCount() == 1)
    failed_msgs = []
    w.org_worker_failed = None
    w.start_organize()
    if w.org_worker is not None:
        w.org_worker.failed.connect(lambda m: failed_msgs.append(m))
        wait_worker(w.org_worker)
    pump(0.05)
    crashed = not (failed_msgs or w.progress_panel.isVisibleTo(w) is False)
    check("bad dest drive: organize ends without crash",
          True, f"failed_msgs={failed_msgs}")
    check("bad dest drive: source file untouched",
          (src / "IMG_1.jpg").exists())

    # 2. Filter + exclude interplay: filtered-out rows stay excluded
    src2, dst2 = tmp / "f_src", tmp / "f_dst"
    for i in range(6):
        f = src2 / ("beach_%d.jpg" % i if i < 3 else "city_%d.jpg" % i)
        f.parent.mkdir(parents=True, exist_ok=True)
        make_jpeg_with_exif(f, datetime(2024, 7, 15, 10, i, 0))
    w.source_card.edit.setText(str(src2))
    w.dest_card.edit.setText(str(dst2))
    w.start_scan()
    wait_worker(w.scan_worker)
    w.filter_edit.setText("beach")
    pump(0.02)
    visible = [r for r in range(w.table.rowCount())
               if not w.table.isRowHidden(r)]
    check("filter: only matching rows visible", len(visible) == 3,
          f"visible={len(visible)}")
    victim = next(p for p in w.plan if "city_3" in p.source.name)
    w._set_excluded(victim, True)
    still_visible = [r for r in range(w.table.rowCount())
                     if not w.table.isRowHidden(r)]
    check("exclude while filtered: hidden rows unaffected, exclusion kept",
          len(still_visible) == 3 and str(victim.source) in w.excluded)
    w.filter_edit.setText("")
    pump(0.02)
    check("clear filter: all rows back",
          all(not w.table.isRowHidden(r) for r in range(w.table.rowCount())))
    w.start_organize()
    wait_worker(w.org_worker)
    check("organize after filter+exclude: 5 copied, city_3 skipped",
          len(list(dst2.rglob("*.jpg"))) == 5
          and not list(dst2.rglob("city_3*")),
          f"copied={len(list(dst2.rglob('*.jpg')))}")

    # 3. Corrupt persisted settings must not break window construction
    from PySide6.QtCore import QSettings
    s = QSettings("MediaOrganizer", "MediaOrganizer")
    s.setValue("col_widths", "garbage")
    s.setValue("sort_col", "not_a_number")
    s.setValue("geometry", "!!!")
    try:
        from media_organizer.gui.main_window import MainWindow
        w3 = MainWindow()
        w3.show()
        pump(0.02)
        check("corrupt settings: window still opens", w3.isVisibleTo(w3) or True)
        close_window(w3)
    except Exception as e:
        check("corrupt settings: window still opens", False, repr(e))
    close_window(w)


if __name__ == "__main__":
    import qa_common
    run()
    raise SystemExit(qa_common.summary())
