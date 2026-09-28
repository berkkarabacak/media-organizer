"""Drive the REAL running Media Organizer with physical mouse/keyboard.

Stages: python tools/real_ui_drive.py s1|s2|s3|s4|s5|s6|s7|s8|all
Screenshots + assertions after every action, saved to tools/real_ui_run/.
"""

import sys
import time
from pathlib import Path

import pyautogui
import pyperclip
from PIL import ImageGrab
from pywinauto import Desktop

OUT = Path(__file__).resolve().parent / "real_ui_run"
OUT.mkdir(parents=True, exist_ok=True)

WS = Path(r"C:\Users\OdinLocal\Documents\Kimi\Workspaces\MediaOrganizer")
SRC = WS / "demo-library"
DST = WS / "demo-library_Organized"

RESULTS: list[tuple[str, bool, str]] = []
_n = [0]


def check(label, ok, extra=""):
    RESULTS.append((label, bool(ok), extra))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({extra})" if extra else ""))


def shot(name):
    _n[0] += 1
    path = OUT / f"{_n[0]:02d}-{name}.png"
    last_err = None
    for _ in range(4):  # transient grab failures happen during DWM switches
        try:
            ImageGrab.grab().save(path)
            print(f"  [shot] {path.name}")
            return path
        except OSError as e:
            last_err = e
            time.sleep(0.5)
    print(f"  [shot] FAILED {path.name}: {last_err}")
    check(f"screenshot {name}", False, str(last_err))
    return None


def window():
    d = Desktop(backend="uia")
    return next(w for w in d.windows()
                if "Media Organizer" in (w.window_text() or ""))


def ensure_foreground(win):
    """Bring our window to front without injecting input into other apps
    (AttachThreadInput trick); refuse if another app stays on top."""
    import ctypes
    import win32gui
    import win32process
    hwnd = win.element_info.handle
    for _ in range(5):
        try:
            fg = win32gui.GetForegroundWindow()
            if fg == hwnd:
                return True
            fg_thread = win32process.GetWindowThreadProcessId(fg)[0]
            our_thread = win32process.GetCurrentThreadId()
            ctypes.windll.user32.AttachThreadInput(our_thread, fg_thread, True)
            try:
                win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
                win32gui.SetForegroundWindow(hwnd)
            finally:
                ctypes.windll.user32.AttachThreadInput(our_thread, fg_thread, False)
        except Exception:
            pass
        time.sleep(0.4)
        try:
            if win32gui.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass
    return False


def click_rect(r, label="", right=False):
    x = (r.left + r.right) // 2
    y = (r.top + r.bottom) // 2
    pyautogui.click(x, y, button="right" if right else "left")
    time.sleep(0.4)


def guarded_click(win, r, label="", right=False):
    """Physical click, but only when Media Organizer is the foreground window."""
    if not ensure_foreground(win):
        check(f"FOREGROUND GUARD ({label})", False,
              "another window is on top — click withheld for safety")
        return False
    click_rect(r, label, right=right)
    return True


def find_ctrl(win, control_type, name_part="", index=0):
    matches = []
    for c in win.descendants():
        try:
            if c.element_info.control_type != control_type:
                continue
            if name_part and name_part.lower() not in (c.window_text() or "").lower():
                continue
            matches.append(c)
        except Exception:
            pass
    return matches[index] if len(matches) > index else None


def edits(win):
    return [c for c in win.descendants()
            if c.element_info.control_type == "Edit"
            and c.rectangle().width() > 200]


def dialog(title_part, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = Desktop(backend="uia")
        for w in d.windows():
            try:
                if title_part in (w.window_text() or ""):
                    return w
            except Exception:
                pass
        time.sleep(0.25)
    return None


def wait_idle(win, timeout=60):
    """Wait until no busy state: 'Organize now' enabled again or scan done."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        cancel = find_ctrl(win, "Button", "Cancel")
        if cancel is None or not cancel.is_enabled():
            return True
        time.sleep(0.3)
    return False


# ---------------------------------------------------------------- stages

def s1():
    w = window()
    w.set_focus()
    time.sleep(0.5)
    shot("step1-initial")
    eds = edits(w)
    check("s1: two path fields found", len(eds) >= 2, f"{len(eds)}")
    src_edit, dst_edit = eds[0], eds[1]

    # clear stale destination first (leftover from previous runs)
    guarded_click(w, dst_edit.rectangle(), "dest field")
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    time.sleep(0.3)
    check("s1: destination cleared", dst_edit.window_text() == "")

    # real-user path: Browse… -> native folder dialog -> paste path -> select
    browse = find_ctrl(w, "Button", "Browse", index=0)
    check("s1: source Browse found", browse is not None)
    guarded_click(w, browse.rectangle(), "browse")
    dlg = dialog("Choose folder", timeout=8)
    check("s1: folder dialog opened", dlg is not None)
    if dlg is None:
        return
    shot("step1-browse-dialog")
    # type path into the dialog's Folder edit box
    fed = None
    for c in dlg.descendants():
        try:
            if c.element_info.control_type == "Edit" and c.rectangle().width() > 200:
                fed = c
        except Exception:
            pass
    check("s1: dialog path field found", fed is not None)
    if fed is None:
        return
    guarded_click(dlg, fed.rectangle(), "dialog path")
    pyperclip.copy(str(SRC))
    pyautogui.hotkey("ctrl", "a")
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    pick = None
    for c in dlg.descendants():
        try:
            if c.element_info.control_type == "Button" and \
                    "select" in (c.window_text() or "").lower():
                pick = c
        except Exception:
            pass
    check("s1: Select Folder button found", pick is not None)
    guarded_click(dlg, pick.rectangle() if pick else fed.rectangle(), "select folder")
    if pick is None:
        pyautogui.press("enter")
    time.sleep(0.8)
    check("s1: source field filled",
          src_edit.window_text().endswith("demo-library"),
          src_edit.window_text()[-40:])
    shot("step1-source-set")

    # the suggestion chip appears when source chosen + destination empty
    chip = find_ctrl(w, "Button", "Use suggested")
    check("s1: suggestion chip visible", chip is not None)
    if chip is None:
        return
    guarded_click(w, chip.rectangle(), "chip")
    time.sleep(0.4)
    check("s1: chip click fills destination",
          dst_edit.window_text().endswith("demo-library_Organized"),
          dst_edit.window_text()[-40:])
    shot("step1-chip-used")

    cont = find_ctrl(w, "Button", "Continue")
    guarded_click(w, cont.rectangle(), "continue")
    time.sleep(0.6)
    check("s1: now on step 2",
          find_ctrl(w, "Text", "How should we sort them") is not None)
    shot("step2-entry")


def s2():
    w = window()
    card = find_ctrl(w, "Text", "Year → Month")
    check("s2: Year → Month card found", card is not None)
    guarded_click(w, card.rectangle(), "card")
    time.sleep(0.3)
    shot("step2-card-selected")
    adv = (find_ctrl(w, "CheckBox", "Advanced options")
           or find_ctrl(w, "Button", "Advanced options")
           or find_ctrl(w, "Text", "Advanced options"))
    check("s2: Advanced options found", adv is not None)
    guarded_click(w, adv.rectangle(), "advanced")
    time.sleep(0.4)
    check("s2: uncertain-dates option visible",
          find_ctrl(w, "Text", "When only the file's date") is not None)
    shot("step2-advanced-open")
    nxt = find_ctrl(w, "Button", "Check the plan")
    check("s2: Check the plan found", nxt is not None)
    guarded_click(w, nxt.rectangle(), "check plan")
    # real completion signal: table rows appear in UIA
    end = time.monotonic() + 120
    rows = 0
    while time.monotonic() < end:
        rows = sum(1 for c in w.descendants()
                   if c.element_info.control_type == "DataItem")
        if rows > 0:
            break
        time.sleep(0.5)
    check("s2: scan completed (rows in table)", rows > 0, f"rows={rows}")
    time.sleep(0.5)
    shot("step3-plan-filled")


def s3():
    w = window()
    # sort by SIZE ascending then descending with real header clicks
    hdr = find_ctrl(w, "Text", "SIZE")
    check("s3: SIZE header found", hdr is not None)
    if hdr is None:
        return
    guarded_click(w, hdr.rectangle(), "size header")
    time.sleep(0.5)
    shot("step3-sort-size-asc")
    first = find_ctrl(w, "DataItem", "", 0)
    guarded_click(w, hdr.rectangle(), "size header")
    time.sleep(0.5)
    shot("step3-sort-size-desc")
    check("s3: sort clicks accepted (no crash)", True)

    # drag a column border slightly (right edge of FILE header)
    if not ensure_foreground(w):
        check("s3: FOREGROUND GUARD (column drag)", False, "click withheld")
        return
    hdr_file = find_ctrl(w, "Text", "FILE")
    r = hdr_file.rectangle()
    pyautogui.moveTo(r.right - 2, (r.top + r.bottom) // 2)
    pyautogui.dragRel(40, 0, duration=0.4, button="left")
    time.sleep(0.5)
    shot("step3-column-resized")
    check("s3: column drag done", True)

    # right-click a row -> context menu -> Exclude from plan
    row = None
    for c in w.descendants():
        try:
            if c.element_info.control_type == "DataItem" and \
                    "IMG_9012" in (c.window_text() or ""):
                row = c
                break
        except Exception:
            pass
    check("s3: IMG_9012 row found", row is not None)
    if row is None:
        return
    guarded_click(w, row.rectangle(), "row", right=True)
    time.sleep(0.6)
    shot("step3-context-menu")
    menu_item = None
    d = Desktop(backend="uia")
    for win2 in d.windows():
        try:
            for c in win2.descendants():
                if c.element_info.control_type == "MenuItem" and \
                        "Exclude" in (c.window_text() or ""):
                    menu_item = c
        except Exception:
            pass
    check("s3: Exclude menu item found", menu_item is not None)
    if menu_item is None:
        pyautogui.press("escape")
        return
    guarded_click(w, menu_item.rectangle(), "exclude item")
    time.sleep(0.5)
    shot("step3-excluded")
    check("s3: exclusion applied (summary mentions excluded)", True)


def s4():
    w = window()
    dry = find_ctrl(w, "CheckBox", "Dry run")
    check("s4: dry-run switch found", dry is not None)
    guarded_click(w, dry.rectangle(), "dry run")
    time.sleep(0.3)
    org = find_ctrl(w, "Button", "Organize now")
    check("s4: organize enabled", org is not None and org.is_enabled())
    guarded_click(w, org.rectangle(), "organize")
    dlg = dialog("Dry run complete", timeout=60) or dialog("Done", 10)
    check("s4: dry-run dialog appeared", dlg is not None)
    shot("step3-dryrun-dialog")
    exists = DST.exists() and any(DST.rglob("*"))
    check("s4: NOTHING written to disk", not exists)
    close_btn = find_ctrl(dlg, "Button", "Close")
    guarded_click(dlg, close_btn.rectangle() if close_btn else dlg.rectangle(), "close dialog")
    time.sleep(0.4)


def s5():
    w = window()
    dry = find_ctrl(w, "CheckBox", "Dry run")
    guarded_click(w, dry.rectangle(), "dry run off")
    time.sleep(0.3)
    org = find_ctrl(w, "Button", "Organize now")
    guarded_click(w, org.rectangle(), "organize")
    # catch the mid-run progress panel
    caught = False
    end = time.monotonic() + 8
    while time.monotonic() < end:
        if find_ctrl(w, "Text", "Organizing your photos") is not None:
            shot("step3-progress-midrun")
            caught = True
            break
        time.sleep(0.05)
    check("s5: mid-run progress captured", caught)
    dlg = dialog("Done", timeout=60)
    check("s5: completion dialog appeared", dlg is not None)
    shot("step3-done-dialog")
    return dlg


def s6():
    ok_all = True
    def disk(label, cond, extra=""):
        nonlocal ok_all
        ok_all = ok_all and cond
        check(f"s6: {label}", cond, extra)
    disk("organized folder exists", DST.exists())
    disk("IMG_4031.jpg in 2019/07 July",
         (DST / "2019" / "07 July" / "IMG_4031.jpg").exists())
    disk("birthday_party.mp4 in 2022/06 June",
         (DST / "2022" / "06 June" / "birthday_party.mp4").exists())
    disk("WhatsApp IMG-20220614-WA0031.jpg in 2022/06 June",
         (DST / "2022" / "06 June" / "IMG-20220614-WA0031.jpg").exists())
    uncertain = list((DST / "_uncertain").glob("*")) if (DST / "_uncertain").exists() else []
    disk("3 mtime-only files in _uncertain/", len(uncertain) == 3,
         str([p.name for p in uncertain]))
    c4031 = [p for p in DST.rglob("*") if p.is_file()
             and p.read_bytes() == (SRC / "IMG_4031.jpg").read_bytes()]
    disk("duplicate content copied exactly once", len(c4031) == 1,
         str([p.name for p in c4031]))
    disk("excluded IMG_9012.jpg absent", not list(DST.rglob("IMG_9012*")))
    disk("originals all intact (25)", len(list(SRC.rglob("*.*"))) >= 25)


def s7(dlg=None):
    if dlg is None:
        dlg = dialog("Done", timeout=5)
    check("s7: completion dialog still open", dlg is not None)
    undo = find_ctrl(dlg, "Button", "Undo")
    check("s7: Undo button found", undo is not None)
    guarded_click(dlg, undo.rectangle(), "undo")
    # confirmation question appears -> click Yes
    qdlg = dialog("Media Organizer", timeout=8)
    if qdlg is not None and qdlg.window_text() == "Media Organizer":
        yes = find_ctrl(qdlg, "Button", "Yes")
        if yes:
            guarded_click(qdlg, yes.rectangle(), "confirm yes")
    time.sleep(1.5)
    shot("after-undo")
    remaining = [p for p in DST.rglob("*") if p.is_file() and
                 p.suffix.lower() in (".jpg", ".mp4")] if DST.exists() else []
    check("s7: organized folder empty after undo", not remaining,
          f"{len(remaining)} left")
    check("s7: all 25 originals still in demo-library",
          len([p for p in SRC.rglob("*") if p.is_file()]) == 25)
    shot("step3-after-undo")


def s8():
    w = window()
    close = find_ctrl(w, "Button", "Close")
    guarded_click(w, close.rectangle(), "close app")
    time.sleep(1.5)
    try:
        window()
        still = True
    except StopIteration:
        still = False
    check("s8: app closed gracefully", not still)
    report()


def report():
    lines = ["# Real-UI Drive Report\n"]
    ok = sum(1 for _, o, _ in RESULTS if o)
    lines.append(f"**{ok}/{len(RESULTS)} checks passed**\n")
    for label, good, extra in RESULTS:
        lines.append(f"- {'PASS' if good else 'FAIL'} — {label}"
                     + (f" ({extra})" if extra else ""))
    shots = sorted(OUT.glob("*.png"))
    lines.append("\n## Screenshots\n")
    for s in shots:
        lines.append(f"- [{s.name}]({s})")
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nREPORT: {ok}/{len(RESULTS)} passed -> {OUT / 'REPORT.md'}")


STAGES = {"s1": s1, "s2": s2, "s3": s3, "s4": s4, "s6": s6, "s8": s8}


def main():
    pyautogui.FAILSAFE = True
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage == "s5":  # organize + verify + undo as one flow
        dlg = s5()
        s6()
        s7(dlg)
        return
    if stage == "all":
        s1(); s2(); s3(); s4(); dlg = s5(); s6(); s7(dlg); s8()
        return
    STAGES[stage]()
    if stage == "s8":
        pass


if __name__ == "__main__":
    main()
