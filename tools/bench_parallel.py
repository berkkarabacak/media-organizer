"""Benchmark: serial vs thread vs process metadata extraction.

Run: .venv/Scripts/python tools/bench_parallel.py
"""

import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    from tests.helpers import make_jpeg_with_exif
    from media_organizer.core.metadata import (analyze_media_batch,
                                               extract_capture_date)
    tmp = Path(tempfile.mkdtemp())
    files = []
    for i in range(2000):
        f = make_jpeg_with_exif(tmp / f"IMG_{i:05d}.jpg",
                                datetime(2024, 7, 15, 10, i % 60, 0))
        with open(f, "ab") as fh:
            fh.write(b"\x00" * (256 * 1024))
        files.append(f)

    t0 = time.monotonic()
    serial = {p: extract_capture_date(p) for p in files}
    ts = time.monotonic() - t0
    print(f"serial:              {ts:.2f}s")

    t0 = time.monotonic()
    par = analyze_media_batch(files)
    tt = time.monotonic() - t0
    ok = all(par[p][0].date == serial[p].date for p in files)
    print(f"threads (32):        {tt:.2f}s  speedup {ts/tt:.2f}x  correct={ok}")

    from concurrent.futures import ProcessPoolExecutor
    t0 = time.monotonic()
    with ProcessPoolExecutor(max_workers=8) as pool:
        res = list(pool.map(extract_capture_date, files, chunksize=50))
    tp = time.monotonic() - t0
    ok = all(r.date == serial[p].date for r, p in zip(res, files))
    print(f"processes (8):       {tp:.2f}s  speedup {ts/tp:.2f}x  correct={ok}")


if __name__ == "__main__":
    main()
