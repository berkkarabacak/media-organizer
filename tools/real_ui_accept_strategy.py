"""Fallback path for strategy selection: the custom strategy cards have no
working UIA InvokePattern (custom QFrame), so select via persisted QSettings
and relaunch the installed app — the same thing a real user's choice does.
"""

import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSettings
from pywinauto import Desktop

EXE = (r"C:\Users\OdinLocal\AppData\Local\Programs\Media Organizer"
       r"\MediaOrganizer.exe")
WS = Path(r"C:\Users\OdinLocal\Documents\Kimi\Workspaces\MediaOrganizer")


def window(timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = Desktop(backend="uia")
        for w in d.windows():
            try:
                if "Media Organizer" in (w.window_text() or ""):
                    return w
            except Exception:
                pass
        time.sleep(0.5)
    return None


def find(win, name, ctype=None):
    for c in win.descendants():
        try:
            if (c.window_text() or "") == name and \
                    (ctype is None or c.element_info.control_type == ctype):
                return c
        except Exception:
            pass
    return None


def main():
    # 1. close the running app via File -> Exit
    w = window()
    if w:
        file_menu = find(w, "File", "MenuItem")
        try:
            file_menu.iface_expand_collapse.Expand()
        except Exception:
            file_menu.iface_invoke.Invoke()
        time.sleep(0.8)
        d = Desktop(backend="uia")
        for win2 in d.windows():
            try:
                for c in win2.descendants():
                    if c.element_info.control_type == "MenuItem" and \
                            (c.window_text() or "") in ("Exit", "E&xit"):
                        c.iface_invoke.Invoke()
                        print("exit invoked")
            except Exception:
                pass
        time.sleep(2)
    r = subprocess.run(["tasklist"], capture_output=True, text=True)
    print("closed:", "MediaOrganizer" not in r.stdout)

    # 2. persist the nested strategy (what the app writes when a user picks it)
    s = QSettings("MediaOrganizer", "MediaOrganizer")
    s.setValue("strategy", "year_quarter_month")
    s.sync()
    print("strategy persisted:", s.value("strategy"))

    # 3. relaunch the installed exe
    subprocess.Popen([EXE],
                     creationflags=subprocess.DETACHED_PROCESS
                     | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
    w = window()
    print("relaunched:", w is not None)
    if not w:
        return 1
    time.sleep(1)
    # Continue -> step 2 -> Check the plan
    btn = find(w, "Continue", "Button")
    if btn:
        btn.iface_invoke.Invoke()
        time.sleep(0.8)
    nxt = find(w, "Check the plan", "Button")
    if nxt:
        nxt.iface_invoke.Invoke()
    end = time.monotonic() + 90
    rows = 0
    while time.monotonic() < end:
        rows = sum(1 for c in w.descendants()
                   if c.element_info.control_type == "DataItem")
        if rows:
            break
        time.sleep(0.5)
    print("rows:", rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
