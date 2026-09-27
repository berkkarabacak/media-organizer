"""Offscreen UI capture: renders the real MainWindow through its 3 steps
and saves PNG screenshots to tools/ui_shots/.

Run: QT_QPA_PLATFORM=offscreen .venv/Scripts/python tools/capture_ui.py
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QMessageBox

from media_organizer.core.metadata import (
    CaptureDate, Confidence, DateSource,
)
from media_organizer.core.organizer import PlannedFile
from media_organizer.gui.main_window import MainWindow
from media_organizer.gui.theme import DARK_QSS

OUT_DIR = Path(__file__).resolve().parent / "ui_shots"
W, H = 1400, 900


def _demo_plan(src: Path, dst: Path):
    """A realistic dozen-row plan: EXIF / filename / video / undated / dupes."""
    rows = [
        ("IMG_2043.jpg", "2024/07 July", datetime(2024, 7, 15, 10, 30, 22),
         DateSource.EXIF, Confidence.HIGH, "EXIF capture date", 4_238_456, False),
        ("IMG_2044.jpg", "2024/07 July", datetime(2024, 7, 15, 10, 31, 5),
         DateSource.EXIF, Confidence.HIGH, "EXIF capture date", 4_102_388, False),
        ("IMG_2044_copy.jpg", None, datetime(2024, 7, 15, 10, 31, 5),
         DateSource.EXIF, Confidence.LOW, "", 4_102_388, True),
        ("PXL_20240211_093012.jpg", "2024/02 February",
         datetime(2024, 2, 11, 9, 30, 12),
         DateSource.FILENAME, Confidence.MEDIUM, "date in filename", 3_874_120, False),
        ("VID_20231225_181500.mp4", "2023/12 December",
         datetime(2023, 12, 25, 18, 15, 0),
         DateSource.VIDEO, Confidence.HIGH, "video container metadata",
         184_520_331, False),
        ("Screenshot_2024-01-15-12-30-00.png", "2024/01 January",
         datetime(2024, 1, 15, 12, 30, 0),
         DateSource.FILENAME, Confidence.MEDIUM, "date in filename", 812_455, False),
        ("holiday_2019.jpg", "2019/08 August", datetime(2019, 8, 7, 14, 2, 11),
         DateSource.MTIME, Confidence.LOW,
         "filesystem modified time (unreliable)", 2_344_981, False),
        ("scan_0001.jpg", "_undated", None, DateSource.NONE, Confidence.LOW,
         "no date found", 1_203_556, False),
        ("scan_0002.jpg", "_undated", None, DateSource.NONE, Confidence.LOW,
         "no date found", 988_120, False),
        ("IMG-20240115-WA0001.jpg", "2024/01 January", datetime(2024, 1, 15),
         DateSource.FILENAME, Confidence.MEDIUM, "date in filename", 654_332, False),
        ("DJI_0042.MP4", "2022/06 June", datetime(2022, 6, 3, 16, 45, 0),
         DateSource.VIDEO, Confidence.HIGH, "video container metadata",
         402_112_900, False),
        ("old_camera_photo.jpg", "2017/03 March", datetime(2017, 3, 9, 8, 12, 44),
         DateSource.EXIF, Confidence.HIGH, "EXIF capture date", 5_511_204, False),
    ]
    plan = []
    for name, sub, dt, source, conf, detail, size, is_dupe in rows:
        capture = CaptureDate(dt, source, conf, detail)
        kind = "video" if name.lower().endswith("mp4") else "image"
        dest = (dst / sub / name) if sub else None
        plan.append(PlannedFile(src / "DCIM" / name, dest, size, capture,
                                kind, is_duplicate=is_dupe))
    return plan


def grab(window, name):
    path = OUT_DIR / name
    window.grab().save(str(path))
    size = path.stat().st_size
    print(f"saved {path}  ({size:,} bytes)")
    assert size > 20_000, f"{name} looks empty!"
    return path


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    src, dst = tmp / "photos", tmp / "photos_Organized"

    app = QApplication(sys.argv)
    # The offscreen QPA plugin ships no font database — register the real
    # Windows fonts so text renders instead of tofu boxes.
    from PySide6.QtGui import QFontDatabase
    for font_file in ("segoeui.ttf", "segoeuib.ttf", "segoeuil.ttf",
                      "consola.ttf", "seguisym.ttf"):
        fid = QFontDatabase.addApplicationFont(f"C:/Windows/Fonts/{font_file}")
        if fid < 0:
            print(f"WARN: could not load {font_file}")
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)
    QMessageBox.exec = lambda self: None
    QMessageBox.clickedButton = lambda self: None

    # start from a clean slate (no persisted folders from previous runs)
    from PySide6.QtCore import QSettings
    QSettings("MediaOrganizer", "MediaOrganizer").clear()

    w = MainWindow()
    w.resize(W, H)

    # ---- Step 1 ----
    w.source_card.edit.setText(str(src))
    w.dest_card.chip.setText(f"Use suggested:  {dst}")
    w.dest_card.chip.setProperty("suggestedPath", str(dst))
    w.dest_card.chip.setVisible(True)
    w._goto_step(0)
    w.show()
    app.processEvents()
    grab(w, "step1.png")

    # ---- Step 2 ----
    w._goto_step(1)
    app.processEvents()
    grab(w, "step2.png")

    # ---- Step 3 with demo plan ----
    w.dest_card.edit.setText(str(dst))
    w._goto_step(2)
    w._on_plan_ready(_demo_plan(src, dst))
    app.processEvents()
    grab(w, "step3.png")

    # ---- Step 3 mid-run progress state (~60% with ETA) ----
    w._set_busy(True, "Organizing…")
    w._show_progress_panel("Organizing your photos…")
    total_bytes = sum(p.size for p in w.plan if not p.is_duplicate)
    done_bytes = int(total_bytes * 0.6)

    class _DemoEstimator:  # fixed values so the shot shows rate + ETA
        def add(self, *a, **k): pass
        def reset(self, *a, **k): pass
        def rate(self, *a, **k): return 18.4 * 1024 * 1024
        def eta_seconds(self, *a, **k): return 125.0

    w._throughput = _DemoEstimator()
    w._on_org_progress(7, 12, "IMG_2043.jpg", done_bytes, total_bytes)
    app.processEvents()
    grab(w, "step3_progress.png")

    print("ALL SCREENSHOTS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
