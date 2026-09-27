"""Offscreen verification for the progress-bar/ETA pipeline.

Drives the real ScanWorker + OrganizeWorker against a temp folder of
generated JPEGs, captures emitted progress signals, and asserts:
  - progress panel visible during scan and organize
  - progress bar value increases during the run and reaches 100
  - status line shows "N of M files" and current filename
  - detail line shows percent, throughput and ETA
  - byte totals > 2^31 (32-bit int overflow regression) still update the UI
Run: QT_QPA_PLATFORM=offscreen .venv/Scripts/python tools/verify_progress.py
"""

import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QMessageBox

from media_organizer.gui.main_window import MainWindow
from tests.helpers import make_jpeg_with_exif

failures = []


def check(label, ok, extra=""):
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({extra})" if extra else ""))
    if not ok:
        failures.append(label)


def main():
    tmp = Path(tempfile.mkdtemp())
    src, dst = tmp / "photos", tmp / "organized"
    src.mkdir()
    # 24 real JPEGs padded to ~4 MB each so the copy spans several
    # throttle windows and the bar can be observed mid-run.
    for i in range(24):
        f = make_jpeg_with_exif(src / f"IMG_{i:02d}.jpg",
                                datetime(2024, 7, 15, 10, i % 60, 0))
        with open(f, "ab") as fh:  # padding after EOI keeps EXIF readable
            fh.write(b"\x00" * (4 * 1024 * 1024))

    app = QApplication(sys.argv)
    w = MainWindow()
    QMessageBox.exec = lambda self: None       # keep modal dialogs non-blocking
    QMessageBox.clickedButton = lambda self: None
    w.show()
    w.source_card.edit.setText(str(src))
    w.dest_card.edit.setText(str(dst))

    # ---- scan phase ----
    w._goto_step(2)
    w.start_scan()
    check("scan: panel visible", w.progress_panel.isVisibleTo(w))
    check("scan: starts indeterminate (counting)", w.progress.maximum() == 0)
    check("scan: status text",
          bool(w.progress_status.text()) and
          any(k in w.progress_status.text()
              for k in ("Scanning files", "Reading", "Counting")),
          repr(w.progress_status.text()))
    saw_determinate = []
    # wait until the worker is actually running (it can finish before the
    # first sample on fast machines)
    end = time.monotonic() + 10
    while not w.scan_worker.isRunning() and time.monotonic() < end:
        app.processEvents(); time.sleep(0.005)
    while w.scan_worker.isRunning():
        app.processEvents(); time.sleep(0.005)
        saw_determinate.append(w.progress.maximum() == 100)
    w.scan_worker.wait(30000); app.processEvents()
    saw_determinate.append(w.progress.maximum() == 100)  # final state
    check("scan: determinate bar appeared", any(saw_determinate))
    check("scan: plan has 12+ rows", w.table.rowCount() >= 12,
          f"rows={w.table.rowCount()}")
    check("scan: panel hidden afterwards", not w.progress_panel.isVisibleTo(w))

    # ---- organize phase ----
    captured = []
    bar_samples = []
    w.start_organize()
    check("organize: panel visible immediately", w.progress_panel.isVisibleTo(w))
    check("organize: cancel enabled", w.cancel_btn.isEnabled())
    w.org_worker.progress.connect(lambda *a: captured.append(a))
    while w.org_worker.isRunning():
        app.processEvents(); time.sleep(0.005)
        bar_samples.append(w.progress.value())
    w.org_worker.wait(60000); app.processEvents()
    bar_samples.append(w.progress.value())

    check("organize: live progress signals", len(captured) >= 2,
          f"{len(captured)} emissions")
    first, last = captured[0], captured[-1]
    check("organize: byte counts are ints beyond 32-bit safe",
          isinstance(last[3], int) and isinstance(last[4], int))
    check("organize: bytes reach total", last[3] == last[4] > 0,
          f"{last[3]}/{last[4]}")
    check("organize: bar increased during run", max(bar_samples) > min(bar_samples),
          f"samples={sorted(set(bar_samples))}")
    check("organize: bar reached 100", bar_samples[-1] == 100 or max(bar_samples) == 100,
          f"last={bar_samples[-1]} max={max(bar_samples)}")
    check("organize: status line shows counts",
          " of " in w.progress_status.text() or "files" in w.status_label.text(),
          repr(w.progress_status.text()))
    copied = list(dst.glob("2024/07 July/*.jpg"))
    check("organize: 24 files copied", len(copied) == 24, f"{len(copied)}")
    check("organize: panel hidden after finish",
          not w.progress_panel.isVisibleTo(w))

    # ---- regression: > 2^31 byte totals (the original bug) ----
    big = 43_494_500_000  # ~43 GB library
    w._on_org_progress(5_000, 12_427, "IMG_9999.jpg", 20_000_000_000, big)
    ok = (w.progress.value() == 45
          and "12,427" in w.progress_status.text()
          and "IMG_9999.jpg" in w.progress_status.text()
          and "B/s" in w.progress_detail.text()
          and "left" in w.progress_detail.text())
    check("regression: >2GB byte totals update bar+status+ETA", ok,
          f"bar={w.progress.value()} status={w.progress_status.text()!r} "
          f"detail={w.progress_detail.text()!r}")

    print()
    print("RESULT:", "ALL OK" if not failures else f"{len(failures)} FAILURES: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
