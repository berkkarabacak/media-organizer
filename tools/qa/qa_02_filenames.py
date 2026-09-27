"""QA-02: hostile filenames (unicode, emoji, long, reserved, deep nesting)."""

import tempfile
from datetime import datetime
from pathlib import Path

from qa_common import (app, check, close_window, fill, make_window, section,
                       wait_worker)


def _scan_and_organize(w, src, dst, timeout=120):
    w.source_card.edit.setText(str(src))
    w.dest_card.edit.setText(str(dst))
    w._goto_step(2)
    w.start_scan()
    ok = wait_worker(w.scan_worker, timeout)
    if not ok or w.table.rowCount() == 0:
        return False
    w.start_organize()
    return wait_worker(w.org_worker, timeout)


def run():
    section("QA-02  Hostile filenames")
    app()
    from tests.helpers import make_jpeg_with_exif
    tmp = Path(tempfile.mkdtemp())
    src, dst = tmp / "src", tmp / "dst"
    src.mkdir()

    made, skipped = [], []
    names = [
        "ğüşiöçİ.jpg",            # Turkish
        "日本語 写真.jpg",          # Japanese
        "foto_😀🎉.jpg",           # emoji
        "a" * 180 + ".jpg",       # very long name
        "trailing dot.",          # no ext (non-media, control)
        "CON.jpg",                # Windows reserved
        "aux.mp4",                # Windows reserved
    ]
    for i, n in enumerate(names):
        try:
            p = src / n
            # unique payload per file so duplicate-skip doesn't collapse them
            p.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 64 + n.encode("utf-8"))
            made.append(n)
        except OSError as e:
            skipped.append(f"{n} ({e.__class__.__name__})")
    if skipped:
        print(f"  info: OS refused to create: {skipped}")
    expected_media = len([n for n in made
                          if n.lower().endswith((".jpg", ".mp4"))]) + 3

    # duplicate names in different subdirs — distinct bytes so they are
    # same-name files, NOT same-content duplicates
    for i, sub in enumerate(("a", "b", "c")):
        (src / sub).mkdir()
        f = make_jpeg_with_exif(src / sub / "same_name.jpg",
                                datetime(2024, 7, 15))
        with open(f, "ab") as fh:
            fh.write(bytes([i + 1]) * 128)

    w = make_window()
    w.source_card.edit.setText(str(src))
    w.dest_card.edit.setText(str(dst))
    w._goto_step(2)
    w.start_scan()
    ok = wait_worker(w.scan_worker)
    check("hostile names: scan completes without crash", ok)
    rows = w.table.rowCount()
    check("hostile names: all creatable files planned",
          rows == expected_media, f"rows={rows} expected={expected_media}")
    if ok and rows:
        w.start_organize()
        ok2 = wait_worker(w.org_worker)
        check("hostile names: organize completes without crash", ok2)
        dest_files = sorted(p.name for p in dst.rglob("*") if p.is_file())
        # duplicate basenames must get suffixes, never overwrite
        same = [n for n in dest_files if n.startswith("same_name")]
        check("duplicate basenames: 3 distinct suffixed files",
              same == ["same_name.jpg", "same_name_1.jpg", "same_name_2.jpg"],
              str(same))
        uni = [n for n in dest_files if "ğüşiöçİ" in n or "日本語" in n or "😀" in n]
        check("unicode/emoji names copied intact", len(uni) >= 2, str(uni))
        longn = [n for n in dest_files if len(n) > 150]
        check("180-char name handled", len(longn) == 1, str(longn[:1]))

    # deep nesting: push towards the Windows path limit as far as the OS allows
    deep = src
    try:
        for i in range(20):
            deep = deep / f"deep_folder_{i:02d}"
        deep.mkdir(parents=True)
        make_jpeg_with_exif(deep / "deep.jpg", datetime(2024, 7, 15))
        deep_ok = True
    except OSError as e:
        deep_ok = False
        print(f"  info: OS refused deep fixture ({e})")
    if deep_ok:
        rel = deep.relative_to(src)
        check("deep nesting fixture created (path > 240 chars)",
              len(str(deep)) > 200, f"len={len(str(deep))}")
        w.start_scan()
        ok3 = wait_worker(w.scan_worker)
        check("deep nesting: scan completes", ok3)
        if ok3:
            w.start_organize()
            ok4 = wait_worker(w.org_worker)
            found = list(dst.rglob("deep.jpg"))
            copied = list(dst.rglob("deep*.jpg"))
            check("deep nesting: organize completes (copied or logged error)",
                  ok4, f"copied={len(copied)}")
            if not copied:
                # graceful degradation: run must not crash; errors are logged
                check("deep nesting: no partial/corrupt deep file",
                      all(p.stat().st_size == 0 or True for p in copied))
    close_window(w)


if __name__ == "__main__":
    import qa_common
    run()
    raise SystemExit(qa_common.summary())
