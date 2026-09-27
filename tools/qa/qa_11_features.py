"""QA-11: v1.4.1 features — freeze regression, parallelism speedup,
crash-journal resume (GUI), dry run writes nothing, atomic-copy guarantee."""

import tempfile
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from qa_common import (app, check, close_window, make_window, pump, section,
                       wait_worker)


def run():
    section("QA-11  v1.4.1 features")
    a = app()
    tmp = Path(tempfile.mkdtemp())
    from tests.helpers import make_jpeg_with_exif

    # ---- freeze regression: 2,000 files, worst GUI tick < 200 ms ----------
    src, dst = tmp / "big_src", tmp / "big_dst"
    src.mkdir()
    for i in range(2000):
        sub = src / f"b{i % 20:02d}"
        sub.mkdir(exist_ok=True)
        (sub / f"IMG_20240115_{i:05d}.jpg").write_bytes(
            b"\xff\xd8\xff\xe0" + i.to_bytes(4, "little") + b"\x00" * 60)
    w = make_window()
    w.source_card.edit.setText(str(src))
    w.dest_card.edit.setText(str(dst))
    w._goto_step(2)
    w.start_scan()
    worst = 0.0
    t0 = time.monotonic()
    while w.scan_worker.isRunning():
        t = time.monotonic()
        a.processEvents()
        worst = max(worst, time.monotonic() - t)
        time.sleep(0.002)
    w.scan_worker.wait(120000)
    # the plan fill happens on the GUI thread now — measure it too
    t = time.monotonic()
    a.processEvents()
    pump(0.05)
    fill_ms = (time.monotonic() - t) * 1000
    scan_s = time.monotonic() - t0
    check("freeze: worst event-loop tick < 200 ms during scan",
          worst < 0.2, f"worst={worst*1000:.0f} ms")
    check("freeze: 2,000-row plan fill < 1 s (was 4.5 s before the fix)",
          fill_ms < 1000, f"fill={fill_ms:.0f} ms")
    check("freeze: full scan+fill of 2,000 files < 15 s", scan_s < 15,
          f"{scan_s:.1f}s")

    # ---- dry run via GUI: writes nothing ----------------------------------
    section("QA-11b  Dry run (GUI)")
    src2, dst2 = tmp / "dry_src", tmp / "dry_dst"
    src2.mkdir()
    for i in range(3):
        make_jpeg_with_exif(src2 / f"IMG_{i}.jpg", datetime(2024, 7, 15, 10, i))
    w2 = make_window()
    w2.source_card.edit.setText(str(src2))
    w2.dest_card.edit.setText(str(dst2))
    w2._goto_step(2)
    w2.start_scan()
    wait_worker(w2.scan_worker)
    w2.dry_run_step3.setChecked(True)
    check("dry run: step-3 switch syncs advanced toggle",
          w2.dry_run_cb.isChecked())
    QTest.mouseClick(w2.organize_btn, Qt.LeftButton)
    wait_worker(w2.org_worker)
    check("dry run: nothing written",
          not dst2.exists() or not any(dst2.rglob("*.jpg")))
    check("dry run: sources untouched",
          len(list(src2.glob("*.jpg"))) == 3)
    from media_organizer.core.plan import load_log
    check("dry run: no operation log written", load_log(dst2) is None)

    # ---- crash mid-copy: kill worker, resume via journal -------------------
    section("QA-11c  Crash resilience (GUI)")
    src3, dst3 = tmp / "cr_src", tmp / "cr_dst"
    src3.mkdir()
    files3 = []
    for i in range(6):
        f = make_jpeg_with_exif(src3 / f"IMG_{i}.jpg", datetime(2024, 7, 15, 10, i))
        with open(f, "ab") as fh:
            fh.write(b"\x00" * (3 * 1024 * 1024))
        files3.append(f)

    w3 = make_window()
    w3.source_card.edit.setText(str(src3))
    w3.dest_card.edit.setText(str(dst3))
    w3._goto_step(2)
    w3.start_scan()
    wait_worker(w3.scan_worker)
    w3.start_organize()
    # interrupt hard once 2 files are done (simulated crash: cancel + journal
    # left unfinished is exactly the crash shape)
    end = time.monotonic() + 60
    while time.monotonic() < end:
        a.processEvents()
        if len(list(dst3.rglob("*.jpg"))) >= 2:
            break
        time.sleep(0.005)
    w3.org_worker.cancel()
    wait_worker(w3.org_worker)
    partial = sorted(p.name for p in dst3.rglob("*.jpg"))
    parts = list(dst3.rglob("*.part"))
    check("crash: no .part files left at final names", not parts, str(parts))
    check("crash: partial run is all-complete files",
          0 < len(partial) < 6, str(partial))
    src_sizes = {f.name: f.stat().st_size for f in files3}
    check("crash: copied files byte-complete",
          all(p.stat().st_size == src_sizes[p.name] for p in dst3.rglob("*.jpg")))

    from media_organizer.core.journal import find_unfinished_journal
    check("crash: unfinished journal detected",
          find_unfinished_journal(dst3) is not None)

    # resume via the GUI (QMessageBox.question -> Yes = Resume)
    QMessageBox.question = staticmethod(lambda *a2, **k: QMessageBox.Yes)
    w3.start_scan()
    wait_worker(w3.scan_worker)
    w3.start_organize()
    wait_worker(w3.org_worker)
    QMessageBox.question = staticmethod(lambda *a2, **k: QMessageBox.No)
    final = sorted(p.name for p in dst3.rglob("*.jpg"))
    check("resume: all 6 files present after resume",
          len(final) == 6, str(final))
    check("resume: journal marked complete",
          find_unfinished_journal(dst3) is None)
    check("resume: no .part leftovers", not list(dst3.rglob("*.part")))

    # ---- free-space preflight blocks a too-big copy (GUI) ------------------
    section("QA-11d  Free-space preflight (GUI)")
    from media_organizer.core.executor import free_space_status
    real = free_space_status(dst3, 1)
    huge_need = real["free"] + 10**12
    check("preflight: oversized copy detected", not free_space_status(
        dst3, huge_need)["ok"])
    # GUI: block — patch the needed-bytes computation by making a fake plan
    w3.start_scan()
    wait_worker(w3.scan_worker)
    import media_organizer.gui.main_window as mw
    orig = mw.free_space_status
    mw.free_space_status = lambda d, n: orig(d, real["free"] + 10**12)
    before = len(list(dst3.rglob("*.jpg")))
    w3.start_organize()
    pump(0.05)
    check("preflight: oversized organize blocked (no worker started)",
          w3.org_worker is None or not w3.org_worker.isRunning())
    check("preflight: nothing copied while blocked",
          len(list(dst3.rglob("*.jpg"))) == before)
    mw.free_space_status = orig

    # ---- parallelism: measured speedup ------------------------------------
    section("QA-11e  Parallelism speedup (measured)")
    from media_organizer.core.metadata import (analyze_media_batch,
                                               extract_capture_date)
    par_src = tmp / "par_src"
    par_src.mkdir()
    par_files = []
    for i in range(1500):   # above the process-pool threshold (400)
        f = make_jpeg_with_exif(par_src / f"IMG_{i:05d}.jpg",
                                datetime(2024, 7, 15, 10, i % 60, 0))
        with open(f, "ab") as fh:
            fh.write(b"\x00" * (256 * 1024))
        par_files.append(f)
    t0 = time.monotonic()
    serial = {p: extract_capture_date(p) for p in par_files}
    t_serial = time.monotonic() - t0
    t0 = time.monotonic()
    par = analyze_media_batch(par_files)
    t_par = time.monotonic() - t0
    speedup = t_serial / t_par if t_par else 0
    check("parallel: same results as serial",
          all(par[p][0].date == serial[p].date for p in par_files))
    # process-pool spawn costs ~0.5 s, so at ~1.5k small files it can be a
    # wash; the win grows with library size (tools/bench_parallel.py: 2.1x at
    # 2k files in isolation). Timing varies with cache/machine state, so the
    # battery asserts correctness only; the numbers are reported, not gated.
    print(f"  MEASURED: serial {t_serial:.2f}s vs process-pool {t_par:.2f}s "
          f"= {speedup:.1f}x speedup on 1,500 files")

    close_window(w)
    close_window(w2)
    close_window(w3)


if __name__ == "__main__":
    import qa_common
    run()
    raise SystemExit(qa_common.summary())
