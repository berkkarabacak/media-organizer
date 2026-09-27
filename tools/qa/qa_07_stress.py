"""QA-07: stress — 2,000 files, progress to 100%, event loop responsive.
QA-08: persistence — settings survive a restart."""

import tempfile
import time
from datetime import datetime
from pathlib import Path

from qa_common import (app, check, close_window, make_window, pump, section,
                       wait_worker)


def run():
    section("QA-07  Stress: 2,000 files")
    a = app()
    tmp = Path(tempfile.mkdtemp())
    src, dst = tmp / "src", tmp / "dst"
    src.mkdir()
    # 2,000 tiny unique files across 20 subfolders (unique bytes -> hashing
    # still runs but no duplicate collapse)
    t0 = time.monotonic()
    for i in range(2000):
        sub = src / f"batch_{i % 20:02d}"
        sub.mkdir(exist_ok=True)
        (sub / f"IMG_20240115_{i:05d}.jpg").write_bytes(
            b"\xff\xd8\xff\xe0" + i.to_bytes(4, "little") + b"\x00" * 60)
    gen_s = time.monotonic() - t0
    print(f"  fixture: 2,000 files in {gen_s:.1f}s")

    w = make_window()
    w.source_card.edit.setText(str(src))
    w.dest_card.edit.setText(str(dst))
    w._goto_step(2)

    t0 = time.monotonic()
    w.start_scan()
    # measure event-loop responsiveness while the worker runs
    worst_tick = 0.0
    pct_seen = set()
    while w.scan_worker.isRunning():
        tick = time.monotonic()
        app().processEvents()
        worst_tick = max(worst_tick, time.monotonic() - tick)
        if w.progress.maximum() == 100:
            pct_seen.add(w.progress.value())
        time.sleep(0.005)
    w.scan_worker.wait(120000)
    pump(0.05)
    scan_s = time.monotonic() - t0
    check("stress: scan of 2,000 completes", w.table.rowCount() == 2000,
          f"rows={w.table.rowCount()} in {scan_s:.1f}s")
    check("stress: progress reached 100%",
          w.progress.value() == 100 or max(pct_seen or {0}) >= 99,
          f"last={w.progress.value()}")
    check("stress: event loop responsive (< 0.5s worst tick)",
          worst_tick < 0.5, f"worst={worst_tick:.3f}s")

    import tracemalloc
    tracemalloc.start()
    t0 = time.monotonic()
    w.start_organize()
    ok = wait_worker(w.org_worker, 300)
    org_s = time.monotonic() - t0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    copied = len(list(dst.rglob("*.jpg")))
    check("stress: organize 2,000 completes", ok and copied == 2000,
          f"copied={copied} in {org_s:.1f}s")
    check("stress: peak python memory < 512 MB", peak < 512 * 1024 * 1024,
          f"peak={peak / 1024 / 1024:.0f} MB")
    close_window(w)

    section("QA-08  Persistence across restart")
    from PySide6.QtCore import QSettings
    tmp2 = Path(tempfile.mkdtemp())
    w = make_window()
    w.source_card.edit.setText(str(tmp2 / "photos"))
    w.dest_card.edit.setText(str(tmp2 / "sorted"))
    w._select_strategy("year_quarter_month")
    w.recursive_cb.setChecked(False)
    w.dupes_cb.setChecked(False)
    w.move_radio.setChecked(True)
    w.videos_cb.setChecked(False)
    w.table.horizontalHeader().resizeSection(0, 321)
    w._sort_col, w._sort_desc = 4, True
    w.close()  # closeEvent persists everything
    w.deleteLater()
    pump(0.02)

    # second window WITHOUT clearing settings (make_window always clears)
    from media_organizer.gui.main_window import MainWindow
    w2 = MainWindow()
    w2.show()
    pump(0.02)
    s = {
        "source": w2.source_card.edit.text() == str(tmp2 / "photos"),
        "dest": w2.dest_card.edit.text() == str(tmp2 / "sorted"),
        "strategy": w2._selected_strategy() == "year_quarter_month",
        "recursive": w2.recursive_cb.isChecked() is False,
        "dupes": w2.dupes_cb.isChecked() is False,
        "move": w2.move_radio.isChecked() is True,
        "videos": w2.videos_cb.isChecked() is False,
        "col0 width": w2.table.horizontalHeader().sectionSize(0) == 321,
        "sort": w2._sort_col == 4 and w2._sort_desc is True,
    }
    for k, v in s.items():
        check(f"persist: {k} restored", v)
    close_window(w2)


if __name__ == "__main__":
    import qa_common
    run()
    raise SystemExit(qa_common.summary())
