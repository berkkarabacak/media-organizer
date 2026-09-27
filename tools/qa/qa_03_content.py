"""QA-03: corrupt/hostile content (truncated EXIF, fake mp4, locked files...)."""

import tempfile
from datetime import datetime
from pathlib import Path

from qa_common import (app, check, close_window, make_window, section,
                       wait_worker)


def run():
    section("QA-03  Corrupt / hostile content")
    app()
    from tests.helpers import make_jpeg_with_exif, make_jpeg_with_gps
    tmp = Path(tempfile.mkdtemp())
    src, dst = tmp / "src", tmp / "dst"
    src.mkdir()

    # fixture zoo — every file must be planned or skipped, never crash
    (src / "zero_byte.jpg").write_bytes(b"")
    (src / "truncated_exif.jpg").write_bytes(b"\xff\xd8\xff\xe1\x00\x10Exif")
    (src / "fake_video.mp4").write_text("this is not a video at all")
    (src / "text_as_png.png").write_text("definitely not a png")
    (src / "fake.heic").write_bytes(b"\x00" * 128)      # unreadable HEIC
    (src / "readme.txt").write_text("non-media control")
    make_jpeg_with_exif(src / "good.jpg", datetime(2024, 7, 15))

    # EXIF with extreme years (Pillow accepts; app must not crash)
    try:
        make_jpeg_with_exif(src / "year_1900.jpg", datetime(1900, 1, 1))
        make_jpeg_with_exif(src / "year_2100.jpg", datetime(2100, 12, 31))
        extreme_years = True
    except Exception as e:
        extreme_years = False
        print(f"  info: extreme-year fixture failed ({e})")

    # GPS with out-of-range/invalid values must be ignored, not crash
    from PIL import Image
    img = Image.new("RGB", (8, 8))
    exif = img.getexif()
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"; gps[2] = (999.0, 99.0, 99.0)   # invalid degrees
    gps[3] = "E"; gps[4] = (999.0, 99.0, 99.0)
    try:
        img.save(src / "gps_invalid.jpg", "JPEG", exif=exif)
    except Exception as e:
        print(f"  info: invalid-GPS fixture failed ({e})")

    # a file locked by another process (exclusive CreateFile, share mode 0)
    lock_target = make_jpeg_with_exif(src / "locked.jpg", datetime(2024, 7, 15))
    locked_handle = None
    try:
        import ctypes
        GENERIC_READ = 0x80000000
        locked_handle = ctypes.windll.kernel32.CreateFileW(
            str(lock_target), GENERIC_READ, 0, None, 3, 0x80, None)
        if locked_handle in (-1, 0):
            locked_handle = None
            print("  info: could not lock file on this OS")
    except Exception as e:
        print(f"  info: locking unavailable ({e})")

    w = make_window()
    w.source_card.edit.setText(str(src))
    w.dest_card.edit.setText(str(dst))
    w._goto_step(2)
    w.start_scan()
    ok = wait_worker(w.scan_worker)
    rows = w.table.rowCount()
    check("corrupt zoo: scan completes without crash", ok)
    check("corrupt zoo: every media file appears in plan",
          rows >= 8, f"rows={rows}")

    # check core-level outcomes for the hostile files
    from media_organizer.core.metadata import extract_capture_date, extract_gps
    check("0-byte jpg: no crash, falls back",
          extract_capture_date(src / "zero_byte.jpg").source.value in
          ("filename", "mtime", "none"))
    check("fake mp4: no crash",
          extract_capture_date(src / "fake_video.mp4").source.value in
          ("filename", "mtime", "none"))
    check("fake heic: no crash",
          extract_capture_date(src / "fake.heic").source.value in
          ("filename", "mtime", "none"))
    check("invalid GPS ignored",
          extract_gps(src / "gps_invalid.jpg") is None)

    w.start_organize()
    ok2 = wait_worker(w.org_worker)
    check("corrupt zoo: organize completes", ok2)
    good = list(dst.rglob("good.jpg"))
    check("good file copied next to corrupt ones", len(good) == 1)
    if locked_handle:
        # locked file must be logged as error, run continues, nothing partial
        partial = [p for p in dst.rglob("locked.jpg")]
        check("locked file: not copied, run unaffected",
                  len(partial) == 0, f"found={len(partial)}")
        import ctypes
        ctypes.windll.kernel32.CloseHandle(locked_handle)
    close_window(w)


if __name__ == "__main__":
    import qa_common
    run()
    raise SystemExit(qa_common.summary())
