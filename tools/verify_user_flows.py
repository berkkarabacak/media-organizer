"""User-like verification for the chip-click and scan-ETA fixes.

Drives the REAL MainWindow offscreen with QTest mouse clicks (no direct
method calls for the flows under test):
  1. chip click: Browse (patched dialog) -> suggestion chip -> click it
  2. scan: real Continue buttons -> determinate progress + ETA observed
  3. organize: real "Organize now" click -> completion dialog appears
Run: QT_QPA_PLATFORM=offscreen .venv/Scripts/python tools/verify_user_flows.py
"""

import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from media_organizer.gui.main_window import MainWindow
from tests.helpers import make_jpeg_with_exif

failures = []


def check(label, ok, extra=""):
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({extra})" if extra else ""))
    if not ok:
        failures.append(label)


def pump(app, seconds=0.0):
    end = time.monotonic() + seconds
    while True:
        app.processEvents()
        if time.monotonic() >= end:
            break
        time.sleep(0.005)


def main():
    app = QApplication(sys.argv)
    for f in ("segoeui.ttf", "segoeuib.ttf", "segoeuil.ttf", "consola.ttf",
              "seguisym.ttf"):
        QFontDatabase.addApplicationFont(f"C:/Windows/Fonts/{f}")

    tmp = Path(tempfile.mkdtemp())
    src = tmp / "photos"
    src.mkdir()
    # ~15 real EXIF photos padded to ~20 MB each so the scan spans several
    # throttle windows (smaller fixtures finish inside one window and the
    # rate label legitimately shows "—", which flakes this harness)
    for i in range(15):
        f = make_jpeg_with_exif(src / f"IMG_{i:02d}.jpg",
                                datetime(2024, 7, 15, 10, i % 60, 0))
        with open(f, "ab") as fh:
            fh.write(b"\x00" * (20 * 1024 * 1024))

    QSettings("MediaOrganizer", "MediaOrganizer").clear()
    dialog_state = {"shown": False}
    QMessageBox.exec = lambda self: dialog_state.__setitem__("shown", True)
    QMessageBox.clickedButton = lambda self: None
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(src))

    w = MainWindow()
    w.show()
    pump(app)

    # ---------------- BUG 1: suggestion chip, full real-click flow --------
    QTest.mouseClick(w.source_card.button, Qt.LeftButton)   # "Browse…"
    pump(app)
    chip = w.dest_card.chip
    check("chip: visible after choosing source", chip.isVisibleTo(w))
    suggested = chip.property("suggestedPath")
    check("chip: suggestion text present", bool(suggested), repr(suggested))
    print(f"  BEFORE chip click: dest field = {w.dest_card.edit.text()!r}")
    QTest.mouseClick(chip, Qt.LeftButton)
    pump(app)
    print(f"  AFTER  chip click: dest field = {w.dest_card.edit.text()!r}")
    check("chip: click sets destination field",
          w.dest_card.edit.text() == suggested,
          repr(w.dest_card.edit.text()))
    check("chip: visibly responds after click",
          "Using suggested" in chip.text(), repr(chip.text()))

    # ---------------- BUG 2: determinate scan with counts + ETA -----------
    QTest.mouseClick(w.next_btn, Qt.LeftButton)   # step 1 -> 2
    pump(app)
    seen_status, seen_detail, saw_determinate = [], [], []
    QTest.mouseClick(w.next_btn, Qt.LeftButton)   # step 2 -> 3, starts scan
    pump(app, 0.02)
    # wait until the worker is actually running (fast scans finish before
    # the first sample otherwise)
    end = time.monotonic() + 10
    while not w.scan_worker.isRunning() and time.monotonic() < end:
        app.processEvents(); time.sleep(0.005)
    while w.scan_worker.isRunning():
        app.processEvents()
        seen_status.append(w.progress_status.text())
        seen_detail.append(w.progress_detail.text())
        saw_determinate.append(w.progress.maximum() == 100)
        time.sleep(0.01)
    w.scan_worker.wait(60000)
    pump(app, 0.05)
    saw_determinate.append(w.progress.maximum() == 100)  # final state
    # the worker's final forced progress event is queued behind finished_plan;
    # give the event loop a second pump and sample the labels after delivery
    pump(app, 0.2)
    seen_status.append(w.progress_status.text())
    seen_detail.append(w.progress_detail.text())
    seen_status.append(w.status_label.text())

    count_lines = [t for t in seen_status if " of " in t and "files" in t]
    eta_lines = [t for t in seen_detail if "files/s" in t and "left" in t]
    check("scan: determinate bar appeared", any(saw_determinate))
    check("scan: 'N of M files' count text seen", bool(count_lines),
          count_lines[-1] if count_lines else "none")
    check("scan: throughput + ETA text seen", bool(eta_lines),
          eta_lines[-1] if eta_lines else "none")
    check("scan: plan built", w.table.rowCount() == 15,
          f"rows={w.table.rowCount()}")
    print(f"  sample status: {count_lines[-1] if count_lines else seen_status!r}")
    print(f"  sample detail: {eta_lines[-1] if eta_lines else '—'}")

    # ---------------- organize via real click ------------------------------
    dialog_state["shown"] = False
    QTest.mouseClick(w.organize_btn, Qt.LeftButton)
    pump(app, 0.02)
    while w.org_worker is not None and w.org_worker.isRunning():
        app.processEvents()
        time.sleep(0.01)
    w.org_worker.wait(60000)
    pump(app, 0.05)
    check("organize: completion dialog appeared", dialog_state["shown"])
    copied = list((tmp / "photos_Organized").glob("2024/07 July/*.jpg"))
    check("organize: files copied to date folder", len(copied) == 15,
          f"{len(copied)}")

    print()
    print("RESULT:", "ALL OK" if not failures else f"{len(failures)} FAILURES: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
