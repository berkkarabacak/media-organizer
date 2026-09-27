"""QA-09: deeper abuse — move mode, rapid double-clicks, type filters,
non-recursive, sorting during busy runs, move-mode collisions."""

import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from qa_common import (app, check, close_window, make_window, pump, section,
                       wait_worker)


def _photos(src, n=4):
    from tests.helpers import make_jpeg_with_exif
    out = []
    for i in range(n):
        f = src / f"IMG_{i}.jpg"
        f.parent.mkdir(parents=True, exist_ok=True)
        out.append(make_jpeg_with_exif(f, datetime(2024, 7, 15, 10, i, 0)))
    return out


def run():
    section("QA-09  Deeper abuse")
    a = app()
    tmp = Path(tempfile.mkdtemp())

    # 1. Move mode: files leave source; collision at dest gets suffix; undo restores
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    src, dst = tmp / "mv_src", tmp / "mv_dst"
    files = _photos(src, 3)
    (dst / "2024" / "07 July").mkdir(parents=True)
    (dst / "2024" / "07 July" / "IMG_0.jpg").write_bytes(b"preexisting")
    w = make_window()
    w.source_card.edit.setText(str(src))
    w.dest_card.edit.setText(str(dst))
    w.move_radio.setChecked(True)
    w._goto_step(2)
    w.start_scan()
    wait_worker(w.scan_worker)
    w.start_organize()
    wait_worker(w.org_worker)
    check("move mode: sources removed", not any(f.exists() for f in files))
    dest_names = sorted(p.name for p in (dst / "2024" / "07 July").glob("*.jpg"))
    check("move mode: preexisting file NOT overwritten (suffix)",
          dest_names == ["IMG_0.jpg", "IMG_0_1.jpg", "IMG_1.jpg", "IMG_2.jpg"],
          str(dest_names))
    pre = (dst / "2024" / "07 July" / "IMG_0.jpg").read_bytes()
    check("move mode: preexisting content intact", pre == b"preexisting")
    from media_organizer.core.plan import load_log, undo_log
    log = load_log(dst)
    r = undo_log(log, dst)
    check("move mode: undo moves files back to source",
          all(f.exists() for f in files) and r["undone"] == 3,
          f"undone={r['undone']}")
    close_window(w)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

    # 2. Rapid double-click on Organize -> exactly one worker
    src2, dst2 = tmp / "dc_src", tmp / "dc_dst"
    _photos(src2, 3)
    w = make_window()
    w.source_card.edit.setText(str(src2))
    w.dest_card.edit.setText(str(dst2))
    w._goto_step(2)
    w.start_scan()
    wait_worker(w.scan_worker)
    QTest.mouseClick(w.organize_btn, Qt.LeftButton)
    pump(0.01)
    first_worker = w.org_worker
    QTest.mouseClick(w.organize_btn, Qt.LeftButton)  # must be a no-op
    pump(0.01)
    check("rapid double-click organize: single worker",
          w.org_worker is first_worker)
    wait_worker(w.org_worker)
    check("rapid double-click organize: copied exactly once",
          len(list(dst2.rglob("*.jpg"))) == 3)

    # 3. Double start_scan protection via Next button spam
    w.start_scan()
    QTest.mouseClick(w.next_btn, Qt.LeftButton)   # hidden/disabled on step 3
    pump(0.01)
    worker_a = w.scan_worker
    check("scan re-entry guarded", w.scan_worker is worker_a)
    w.cancel_work()
    wait_worker(w.scan_worker)

    # 4. Videos-only / images-only filtering
    from tests.helpers import make_mp4
    src3, dst3 = tmp / "ty_src", tmp / "ty_dst"
    _photos(src3, 2)
    make_mp4(src3 / "clip.mp4", datetime(2024, 7, 15))
    w.source_card.edit.setText(str(src3))
    w.dest_card.edit.setText(str(dst3))
    w.images_cb.setChecked(False)
    w.videos_cb.setChecked(True)
    w.start_scan()
    wait_worker(w.scan_worker)
    check("videos-only: only mp4 planned",
          w.table.rowCount() == 1, f"rows={w.table.rowCount()}")
    w.images_cb.setChecked(True)
    w.videos_cb.setChecked(False)
    w.start_scan()
    wait_worker(w.scan_worker)
    check("images-only: only jpgs planned",
          w.table.rowCount() == 2, f"rows={w.table.rowCount()}")
    w.videos_cb.setChecked(True)

    # 5. Non-recursive mode skips subfolders
    (src3 / "sub").mkdir(exist_ok=True)
    _photos(src3 / "sub", 2)
    w.recursive_cb.setChecked(False)
    w.start_scan()
    wait_worker(w.scan_worker)
    check("non-recursive: subfolder files excluded",
          w.table.rowCount() == 3, f"rows={w.table.rowCount()}")  # 2 jpg + 1 mp4
    w.recursive_cb.setChecked(True)

    # 6. Sorting during an active organize must not corrupt the run
    src4, dst4 = tmp / "sb_src", tmp / "sb_dst"
    files4 = _photos(src4, 8)
    for f in files4:
        with open(f, "ab") as fh:
            fh.write(b"\x00" * (6 * 1024 * 1024))
    w.source_card.edit.setText(str(src4))
    w.dest_card.edit.setText(str(dst4))
    w.start_scan()
    wait_worker(w.scan_worker)
    w.start_organize()
    w._on_header_clicked(4)   # sort by size mid-run
    w._on_header_clicked(0)   # then by name mid-run
    pump(0.01)
    ok = wait_worker(w.org_worker)
    check("sort during organize: run unaffected",
          ok and len(list(dst4.rglob("*.jpg"))) == 8)
    close_window(w)


if __name__ == "__main__":
    import qa_common
    run()
    raise SystemExit(qa_common.summary())
