"""QA-12: _uncertain option — aside vs use, GUI toggle + rescan."""

import os
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from qa_common import (app, check, close_window, make_window, pump, section,
                       wait_worker)


def _fixture(src: Path):
    from tests.helpers import make_jpeg_with_exif
    src.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        make_jpeg_with_exif(src / f"good_{i}.jpg",
                            datetime(2024, 7, 15, 10, i, 0))
    for i in range(2):  # no date anywhere except mtime
        f = src / f"mystery_{i}.jpg"
        f.write_bytes(b"\xff\xd8\xff" + bytes([i]) * 64)
        ts = datetime(2019, 3, 2, 10, i, 0).timestamp()
        os.utime(f, (ts, ts))


def run():
    section("QA-12  Uncertain (mtime-guess) option")
    a = app()
    tmp = Path(tempfile.mkdtemp())

    # --- aside: guesses set aside, EXIF in date folders ---------------------
    src, dst = tmp / "src_a", tmp / "dst_a"
    _fixture(src)
    w = make_window()
    w.source_card.edit.setText(str(src))
    w.dest_card.edit.setText(str(dst))
    check("default: combo is 'aside'",
          w.uncertain_combo.currentData() == "aside")
    w._goto_step(2)
    w.start_scan()
    wait_worker(w.scan_worker)
    w.start_organize()
    wait_worker(w.org_worker)
    uncertain = sorted(p.name for p in (dst / "_uncertain").glob("*.jpg"))
    dated = sorted(p.name for p in (dst / "2024" / "07 July").glob("*.jpg"))
    check("aside: mtime guesses land in _uncertain/",
          uncertain == ["mystery_0.jpg", "mystery_1.jpg"], str(uncertain))
    check("aside: EXIF files in date folders",
          dated == ["good_0.jpg", "good_1.jpg", "good_2.jpg"], str(dated))
    check("aside: summary says 'set aside'",
          "2 uncertain dates set aside" in w.plan_summary.text(),
          w.plan_summary.text())

    # --- use: guesses filed by file date ------------------------------------
    src2, dst2 = tmp / "src_u", tmp / "dst_u"
    _fixture(src2)
    w.source_card.edit.setText(str(src2))
    w.dest_card.edit.setText(str(dst2))
    w.uncertain_combo.setCurrentIndex(
        w.uncertain_combo.findData("use"))
    w.start_scan()
    wait_worker(w.scan_worker)
    w.start_organize()
    wait_worker(w.org_worker)
    used = sorted(p.name for p in (dst2 / "2019" / "03 March").glob("*.jpg"))
    check("use: guesses filed under 2019/03 March (file date)",
          used == ["mystery_0.jpg", "mystery_1.jpg"], str(used))
    check("use: no _uncertain folder created",
          not (dst2 / "_uncertain").exists())
    check("use: summary says dates used",
          "2 uncertain dates used" in w.plan_summary.text(),
          w.plan_summary.text())

    # --- QTest: toggling the combo + rescan updates plan destinations -------
    src3 = tmp / "src_t"
    _fixture(src3)
    dst3 = tmp / "dst_t"
    w.source_card.edit.setText(str(src3))
    w.dest_card.edit.setText(str(dst3))
    w.uncertain_combo.setCurrentIndex(w.uncertain_combo.findData("aside"))
    w.start_scan()
    wait_worker(w.scan_worker)
    dests_aside = {w.table.item(r, 3).text()
                   for r in range(w.table.rowCount())}
    check("toggle: aside plan shows _uncertain rows",
          any("_uncertain" in d for d in dests_aside), str(dests_aside))

    # user goes Back to step 2, expands Advanced, changes the option,
    # then Continue re-scans — the full real flow
    QTest.mouseClick(w.back_btn, Qt.LeftButton)
    pump(0.02)
    check("back to step 2", w.stack.currentIndex() == 1)
    QTest.mouseClick(w.advanced_toggle, Qt.LeftButton)
    pump(0.02)
    check("advanced panel opens", w.advanced_panel.isVisibleTo(w))
    w.uncertain_combo.setCurrentIndex(w.uncertain_combo.findData("use"))
    pump(0.02)
    QTest.mouseClick(w.next_btn, Qt.LeftButton)   # "Check the plan" -> rescan
    wait_worker(w.scan_worker)
    dests_use = {w.table.item(r, 3).text()
                 for r in range(w.table.rowCount())}
    check("toggle: rescan shows date-folder rows instead",
          not any("_uncertain" in d for d in dests_use)
          and any("2019/03 March" in d for d in dests_use),
          str(dests_use))

    # persistence
    w._save_settings()
    from media_organizer.gui.main_window import MainWindow
    w2 = MainWindow()   # no settings clear — must restore what we saved
    w2.show()
    pump(0.02)
    check("persistence: 'use' survives restart",
          w2.uncertain_combo.currentData() == "use")
    close_window(w)
    close_window(w2)


if __name__ == "__main__":
    import qa_common
    run()
    raise SystemExit(qa_common.summary())
