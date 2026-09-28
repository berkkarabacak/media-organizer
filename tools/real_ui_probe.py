"""Probe: find the running Media Organizer window and screenshot it."""

import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent / "real_ui_run"
OUT.mkdir(parents=True, exist_ok=True)

from pywinauto import Desktop
from PIL import ImageGrab


def find_window():
    desktop = Desktop(backend="uia")
    for w in desktop.windows():
        try:
            title = w.window_text()
        except Exception:
            continue
        if "Media Organizer" in title:
            return w
    return None


def shot(name):
    path = OUT / name
    img = ImageGrab.grab()
    img.save(path)
    print(f"saved {path} ({path.stat().st_size:,} bytes)")


def main():
    w = find_window()
    if w is None:
        print("WINDOW NOT FOUND")
        return 1
    print("found:", w.window_text(), w.rectangle())
    w.set_focus()
    time.sleep(0.5)
    shot("01-initial.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
