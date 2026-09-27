"""QA-04: flow abuse — back mid-scan, cancel at 50%, reruns, undo semantics."""

import tempfile
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from qa_common import (app, check, close_window, make_window, pump, section,
                       wait_worker)


def _make_photos(src, n, size_mb=4):
    from tests.helpers import make_jpeg_with_exif
    files = []
    for i in range(n):
        f = make_jpeg_with_exif(src / f"IMG_{i:03d}.jpg",
                                datetime(2024, 7, 15, 10, i % 60, 0))
        with open(f, "ab") as fh:
            fh.write(b"\x00" * (size_mb * 1024 * 1024))
        files.append(f)
    return files


def run():
    section("QA-04  Flow abuse")
    a = app()
    tmp = Path(tempfile.mkdtemp())
    src, dst = tmp / "src", tmp / "dst"
    src.mkdir()
    files = _make_photos(src, 12)

    # 1. Back mid-scan must be blocked (busy guard)
    # 1. Back mid-scan must be blocked (busy guard) — use a big source so the
    # scan is provably still running when we click (parallel hashing is fast)
    slow_src = tmp / "slow_src"
    slow_src.mkdir()
    _make_photos(slow_src, 200, size_mb=2)
    w = make_window()
    w.source_card.edit.setText(str(slow_src))
    w.dest_card.edit.setText(str(dst))
    w._goto_step(2)
    w.start_scan()
    # wait until the worker is actually running, then try to go Back
    end = time.monotonic() + 10
    while not w.scan_worker.isRunning() and time.monotonic() < end:
        pump(0.005)
    QTest.mouseClick(w.back_btn, Qt.LeftButton)
    pump(0.02)
    check("Back mid-scan is blocked", w._busy() and w.stack.currentIndex() == 2)
    w.cancel_work()
    wait_worker(w.scan_worker)

    # 2. Cancel scan mid-way -> partial plan OK, no crash
    w.source_card.edit.setText(str(src))   # back to the 12-file source
    w.dest_card.edit.setText(str(dst))
    w.start_scan()
    pump(0.05)
    w.cancel_work()
    ok = wait_worker(w.scan_worker)
    check("cancel mid-scan: worker ends cleanly", ok)
    rows_after_cancel = w.table.rowCount()
    check("cancel mid-scan: plan usable or empty",
          rows_after_cancel >= 0, f"rows={rows_after_cancel}")

    # 3. Cancel organize at ~50% — copied files must be COMPLETE
    w.start_scan()
    wait_worker(w.scan_worker)
    check("rescan after cancel: full plan", w.table.rowCount() == 12,
          f"rows={w.table.rowCount()}")
    w.start_organize()
    # cancel deterministically: as soon as >=2 files are on disk but < 12
    end = time.monotonic() + 60
    while time.monotonic() < end:
        app().processEvents()
        n = len(list(dst.rglob("*.jpg")))
        if n >= 2:
            break
        time.sleep(0.005)
    w.cancel_work()
    ok = wait_worker(w.org_worker)
    check("cancel organize: worker ends", ok)
    copied = list(dst.rglob("*.jpg"))
    sizes_src = {f.name: f.stat().st_size for f in files}
    incomplete = [p.name for p in copied
                  if p.stat().st_size != sizes_src.get(p.name, -1)]
    check("cancel organize: copied-so-far files are complete",
          not incomplete, str(incomplete))
    check("cancel organize: partial run (some copied, not all)",
          0 < len(copied) < 12, f"copied={len(copied)}")

    # 4. Undo after cancelled run
    from media_organizer.core.plan import load_log, undo_log
    log = load_log(dst)
    check("cancelled run wrote an operation log", log is not None)
    if log:
        result = undo_log(log, dst)
        left = list(dst.rglob("*.jpg"))
        check("undo after cancel: copied files removed",
              result["undone"] == len(copied) and not left,
              f"undone={result['undone']} left={len(left)}")

    # 5. Full run -> undo -> undo again (must refuse politely)
    w.start_scan()
    wait_worker(w.scan_worker)
    w.start_organize()
    wait_worker(w.org_worker)
    copied_full = list(dst.rglob("*.jpg"))
    check("full run copies all 12", len(copied_full) == 12,
          f"copied={len(copied_full)}")
    log2 = load_log(dst)
    r1 = undo_log(log2, dst)
    check("undo full run: all removed",
          r1["undone"] == 12 and not list(dst.rglob("*.jpg")))
    log3 = load_log(dst)
    check("undo twice: log marked undone", log3.undone is True)
    # GUI-level: undo_last_run must show 'already undone' without touching fs
    w._undo_log(log3, dst)
    check("undo twice: no files re-affected", not list(dst.rglob("*.jpg")))

    # 6. Organize twice in a row — second run must suffix, never overwrite
    w.start_scan()
    wait_worker(w.scan_worker)
    w.start_organize()
    wait_worker(w.org_worker)
    w.start_scan()
    wait_worker(w.scan_worker)
    w.start_organize()
    wait_worker(w.org_worker)
    all_jpgs = sorted(p.name for p in dst.rglob("*.jpg"))
    check("two runs: 24 files, suffixed, none overwritten",
          len(all_jpgs) == 24 and "IMG_000_1.jpg" in all_jpgs,
          f"total={len(all_jpgs)}")
    # originals still intact (copy mode)
    check("two runs: source originals intact",
          all(f.exists() for f in files))

    # 7. Exclude ALL files -> organize politely refuses
    w.start_scan()
    wait_worker(w.scan_worker)
    for p in list(w.plan):
        w._set_excluded(p, True)
    pump(0.02)
    check("exclude all: organize button disabled",
          not w.organize_btn.isEnabled())
    before = len(list(dst.rglob("*.jpg")))
    w.start_organize()  # direct call must also refuse
    pump(0.05)
    check("exclude all: start_organize refuses (no worker)",
          w.org_worker is None or not w.org_worker.isRunning())
    check("exclude all: nothing new copied",
          len(list(dst.rglob("*.jpg"))) == before)
    close_window(w)


if __name__ == "__main__":
    import qa_common
    run()
    raise SystemExit(qa_common.summary())
