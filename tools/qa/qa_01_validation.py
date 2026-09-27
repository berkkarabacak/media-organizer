"""QA-01: input validation flows (real MainWindow, QTest clicks)."""

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from qa_common import app, check, close_window, make_window, pump, section, wait_worker


def run():
    section("QA-01  Input validation")
    a = app()
    tmp = Path(tempfile.mkdtemp())

    # 1. Continue with no source/destination -> refused, stays on step 1
    w = make_window()
    QTest.mouseClick(w.next_btn, Qt.LeftButton)
    pump(0.05)
    check("no source: Continue refused (stays on step 1)",
          w.stack.currentIndex() == 0)

    # 2. Source that doesn't exist -> refused
    w.source_card.edit.setText(str(tmp / "ghost"))
    w.dest_card.edit.setText(str(tmp / "dst"))
    QTest.mouseClick(w.next_btn, Qt.LeftButton)
    pump(0.05)
    check("nonexistent source: refused", w.stack.currentIndex() == 0)

    # 3. Source ok, no destination -> refused
    src = tmp / "src"
    src.mkdir()
    w.source_card.edit.setText(str(src))
    w.dest_card.edit.setText("")
    QTest.mouseClick(w.next_btn, Qt.LeftButton)
    pump(0.05)
    check("no destination: refused", w.stack.currentIndex() == 0)

    # 4. Empty folder -> scan completes, 0 rows, organize disabled
    w.dest_card.edit.setText(str(tmp / "dst"))
    w._goto_step(2)
    w.start_scan()
    ok = wait_worker(w.scan_worker)
    check("empty folder: scan completes", ok and w.table.rowCount() == 0,
          f"rows={w.table.rowCount()}")
    check("empty folder: organize disabled", not w.organize_btn.isEnabled())
    check("empty folder: empty-state hint shown",
          "No files" in w.plan_summary.text(), w.plan_summary.text())

    # 5. Only non-media files -> same as empty
    (src / "notes.txt").write_text("hello")
    (src / "data.csv").write_text("1,2,3")
    QTest.mouseClick(w.organize_btn, Qt.LeftButton)  # disabled: must no-op
    w.start_scan()
    wait_worker(w.scan_worker)
    check("non-media only: 0 rows, organize disabled",
          w.table.rowCount() == 0 and not w.organize_btn.isEnabled())

    # 6. Destination INSIDE source: files must not self-copy or recurse
    from tests.helpers import make_jpeg_with_exif
    from datetime import datetime
    make_jpeg_with_exif(src / "IMG_1.jpg", datetime(2024, 7, 15))
    inner_dst = src / "organized"
    w.dest_card.edit.setText(str(inner_dst))
    w.start_scan()
    wait_worker(w.scan_worker)
    check("dest inside source: scan finds the source file",
          w.table.rowCount() == 1, f"rows={w.table.rowCount()}")
    w.start_organize()
    wait_worker(w.org_worker)
    copied1 = sorted(p.name for p in inner_dst.rglob("*.jpg"))
    check("dest inside source: first run copies once",
          copied1 == ["IMG_1.jpg"], str(copied1))
    # second run: organized copies must NOT be re-scanned (no doubling loop)
    w.start_scan()
    wait_worker(w.scan_worker)
    check("dest inside source: organized copies excluded from re-scan",
          w.table.rowCount() == 1, f"rows={w.table.rowCount()}")
    w.start_organize()
    wait_worker(w.org_worker)
    names2 = sorted(p.name for p in inner_dst.rglob("*.jpg"))
    check("dest inside source: second run suffixes, never overwrites",
          names2 == ["IMG_1.jpg", "IMG_1_1.jpg"], str(names2))

    # 7. source == destination -> everything excluded from scan, nothing moves
    w.dest_card.edit.setText(str(src))
    w.start_scan()
    wait_worker(w.scan_worker)
    check("source == destination: plan empty (self-copy blocked)",
          w.table.rowCount() == 0, f"rows={w.table.rowCount()}")
    check("source == destination: originals untouched",
          (src / "IMG_1.jpg").exists())
    close_window(w)


if __name__ == "__main__":
    import qa_common
    run()
    raise SystemExit(qa_common.summary())
