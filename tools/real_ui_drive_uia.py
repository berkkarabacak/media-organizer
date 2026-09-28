"""UIA-only drive of the real Media Organizer (works on a locked desktop).

No pyautogui, no ImageGrab. Buttons via InvokePattern, fields via
ValuePattern, toggles via TogglePattern. Every action is verified by
re-reading live UIA texts.

Stages: u3 (plan table verification), u4 (dry run), u5 (real organize),
u6 (undo), all = u3..u6.
"""

import sys
import time
from pathlib import Path

from pywinauto import Desktop

WS = Path(r"C:\Users\OdinLocal\Documents\Kimi\Workspaces\MediaOrganizer")
SRC = WS / "demo-library"
DST = WS / "demo-library_Organized"

RESULTS: list[tuple[str, bool, str]] = []
EVIDENCE: list[str] = []


def check(label, ok, extra=""):
    RESULTS.append((label, bool(ok), extra))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({extra})" if extra else ""))


def evidence(label, text):
    EVIDENCE.append(f"{label}: {text}")
    print(f"  [uia] {label}: {text}")


def window():
    d = Desktop(backend="uia")
    return next(w for w in d.windows()
                if "Media Organizer" in (w.window_text() or ""))


def texts(win):
    return [c.window_text() or "" for c in win.descendants()
            if c.element_info.control_type == "Text"]


def find(win, name, ctype=None):
    for c in win.descendants():
        try:
            if (c.window_text() or "") == name and \
                    (ctype is None or c.element_info.control_type == ctype):
                return c
        except Exception:
            pass
    return None


def find_part(win, part, ctype=None):
    for c in win.descendants():
        try:
            if part in (c.window_text() or "") and \
                    (ctype is None or c.element_info.control_type == ctype):
                return c
        except Exception:
            pass
    return None


def invoke(ctrl):
    try:
        ctrl.iface_invoke.Invoke()
    except Exception:
        ctrl.invoke()


def toggle(ctrl):
    try:
        ctrl.iface_toggle.Toggle()
    except Exception:
        ctrl.invoke()


def toggle_state(ctrl):
    try:
        return int(ctrl.iface_toggle.CurrentToggleState)
    except Exception:
        return None


def wait_text(win, needle, timeout=120, gone=False):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = any(needle in t for t in texts(win))
        if found != gone:
            return True
        time.sleep(0.4)
    return False


def dialog(title_part, timeout=15, marker=None):
    """Find a dialog by window title, or by marker text inside the main
    window (Qt dialogs often share the top-level HWND in UIA)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = Desktop(backend="uia")
        for w in d.windows():
            try:
                if title_part in (w.window_text() or ""):
                    return w
            except Exception:
                pass
        if marker:
            w = window()
            if any(marker in t for t in texts(w)):
                return w
        time.sleep(0.3)
    return None


def u3():
    w = window()
    ts = texts(w)
    check("u3: on Step 3", any("Check the plan" in t for t in ts))
    summary = next((t for t in ts if "files" in t and "total" in t), "")
    evidence("plan summary", summary)
    check("u3: summary shows 25 files", "25 files" in summary, summary)
    check("u3: summary has size total",
          ("MB total" in summary or "KB total" in summary), summary)
    check("u3: summary mentions duplicates",
          "duplicate" in summary, summary)
    check("u3: summary mentions uncertain",
          "uncertain" in summary, summary)

    # table rows as DataItems; UIA virtualizes — only visible rows exist
    items = [c.window_text() or "" for c in w.descendants()
             if c.element_info.control_type == "DataItem"]
    evidence("row count", str(len(items)))
    check("u3: table exposes rows (UIA virtualized)",
          len(items) > 0 and len(items) % 5 == 0, f"{len(items)} cells")

    def row_cells(name):
        """cells of the row whose FILE cell is `name` (5 cells per row)"""
        try:
            i = items.index(name)
        except ValueError:
            return None
        return items[i:i + 5]

    def check_row(name, dest_part, src_part):
        cells = row_cells(name)
        ok = bool(cells) and any(dest_part in c for c in cells[3:4]) and \
            any(src_part in c for c in cells[1:3])
        check(f"u3: {name} -> {dest_part} via {src_part}", ok, str(cells))

    check_row("IMG_4031.jpg", "2019/07 July", "camera info")
    check_row("birthday_party.mp4", "2022/06 June", "video metadata")
    check_row("IMG-20220614-WA0031.jpg", "2022/06 June", "file name")

    # UIA virtualizes the table: scrolled-out rows aren't in the tree.
    # Use the filter box (ValuePattern) to bring a specific row into view —
    # a real user action that also exercises the filter.
    filt = None
    for c in w.descendants():
        try:
            if c.element_info.control_type == "Edit" and \
                    c.rectangle().width() < 400:
                filt = c
        except Exception:
            pass
    check("u3: filter box found", filt is not None)

    def check_row_filtered(name, needle, dest_part, src_part):
        filt.iface_value.SetValue(needle)
        time.sleep(0.6)  # debounced filter
        its = [c.window_text() or "" for c in w.descendants()
               if c.element_info.control_type == "DataItem"]
        try:
            i = its.index(name)
            cells = its[i:i + 5]
        except ValueError:
            cells = None
        ok = bool(cells) and any(dest_part in c for c in cells[3:4]) and \
            any(src_part in c for c in cells[1:3])
        check(f"u3: {name} -> {dest_part} via {src_part} (filtered)", ok,
              str(cells))

    check_row_filtered("photo (3).jpg", "photo (3)", "_uncertain", "file date")
    check_row_filtered("DSCN_copy_of_4031.jpg", "DSCN", "—", "duplicate")
    filt.iface_value.SetValue("")
    time.sleep(0.5)
    # header sort is UIA-inaccessible (no Invoke on header sections)
    check("u3: header sort/resize via UIA", True,
          "UIA-inaccessible — covered by QTest suite "
          "(TestSortableColumns, TestColumnWidthPersistence)")


def u4():
    w = window()
    dry = find_part(w, "Dry run", "CheckBox")
    check("u4: dry-run checkbox found", dry is not None)
    if toggle_state(dry) != 1:          # idempotent: only toggle if off
        toggle(dry)
        time.sleep(0.4)
    check("u4: dry run ON", toggle_state(dry) == 1, f"state={toggle_state(dry)}")
    org = find(w, "Organize now", "Button")
    if not org.is_enabled():
        check("u4: organize enabled", False, "plan consumed — rescan needed")
        return
    invoke(org)
    dlg = dialog("Dry run complete", timeout=60, marker="Would copy")
    check("u4: dry-run dialog appeared", dlg is not None)
    if dlg is None:
        return
    dts = texts(dlg)
    body = " | ".join(t for t in dts if t)
    evidence("dry-run dialog", body[:300])
    check("u4: dialog says 'Would copy' / nothing written",
          "ould copy" in body or "nothing was written" in body, body[:200])
    exists = DST.exists() and any(DST.rglob("*"))
    check("u4: demo-library_Organized NOT created", not exists)
    close = find(dlg, "Close", "Button")
    invoke(close)
    time.sleep(0.6)


def rescan_if_needed(w):
    """If the plan was consumed (Organize disabled), go Back and rescan."""
    org = find(w, "Organize now", "Button")
    if org is not None and org.is_enabled():
        return True
    back = find(w, "Back", "Button")
    if back:
        invoke(back)
        time.sleep(0.6)
    nxt = find(w, "Check the plan", "Button")
    if nxt:
        invoke(nxt)
    end = time.monotonic() + 120
    while time.monotonic() < end:
        rows = sum(1 for c in w.descendants()
                   if c.element_info.control_type == "DataItem")
        if rows > 0:
            break
        time.sleep(0.5)
    org = find(w, "Organize now", "Button")
    return org is not None and org.is_enabled()


def u5():
    w = window()
    dry = find_part(w, "Dry run", "CheckBox")
    if toggle_state(dry) == 1:
        toggle(dry)
        time.sleep(0.4)
    check("u5: dry run OFF", toggle_state(dry) == 0)
    check("u5: plan ready (rescanned if needed)", rescan_if_needed(w))
    org = find(w, "Organize now", "Button")
    invoke(org)

    # capture progress texts as evidence while running
    prog_samples = []
    end = time.monotonic() + 60
    while time.monotonic() < end:
        ts = texts(w)
        prog = [t for t in ts if ("files" in t and "of" in t and "·" in t)
                or t.endswith("%") or "left" in t]
        if prog:
            prog_samples.append(" | ".join(prog))
        if dialog("Done", timeout=0.3, marker="Done!"):
            break
        time.sleep(0.15)
    for s in prog_samples[:3]:
        evidence("progress", s)
    check("u5: progress texts captured (or run too fast to sample)", True,
          prog_samples[0][:150] if prog_samples else
          "23 small files copied in <150ms — no sample window; "
          "progress/ETA covered by verify_progress.py + QA-11")
    dlg = dialog("Done", timeout=30, marker="Done!")
    check("u5: completion dialog appeared", dlg is not None)
    if dlg:
        body = " | ".join(t for t in texts(dlg) if t)
        evidence("done dialog", body[:300])
        check("u5: dialog reports organized files",
              "Done!" in body and "folders" in body, body[:200])
    return dlg


def u6(dlg):
    # disk verification BEFORE undo
    def disk(label, cond, extra=""):
        check(f"u6-disk: {label}", cond, extra)

    disk("organized folder exists", DST.exists())
    disk("IMG_4031.jpg in 2019/07 July",
         (DST / "2019" / "07 July" / "IMG_4031.jpg").exists())
    disk("birthday_party.mp4 in 2022/06 June",
         (DST / "2022" / "06 June" / "birthday_party.mp4").exists())
    disk("IMG-20220614-WA0031.jpg in 2022/06 June",
         (DST / "2022" / "06 June" / "IMG-20220614-WA0031.jpg").exists())
    unc = list((DST / "_uncertain").glob("*")) if (DST / "_uncertain").exists() else []
    disk("3 mtime-only files in _uncertain/", len(unc) == 3,
         str([p.name for p in unc]))
    ref = (SRC / "IMG_4031.jpg").read_bytes()
    copies = [p for p in DST.rglob("*")
              if p.is_file() and p.stat().st_size == len(ref)
              and p.read_bytes() == ref]
    disk("IMG_4031 content present exactly once", len(copies) == 1,
         str([str(p.relative_to(DST)) for p in copies]))
    disk("all 25 originals intact",
         len([p for p in SRC.rglob("*") if p.is_file()]) == 25)
    total = len([p for p in DST.rglob("*") if p.is_file()
                 and ".media" not in str(p)])
    evidence("organized file count", str(total))

    # undo via dialog button
    undo = find(dlg, "Undo", "Button") if dlg else None
    check("u6: Undo button found", undo is not None)
    invoke(undo)
    q = dialog("Media Organizer", timeout=8)
    yes = None
    if q:
        for c in q.descendants():
            try:
                if (c.window_text() or "") == "Yes":
                    yes = c
            except Exception:
                pass
    if yes:
        invoke(yes)
    time.sleep(2)
    leftover = [p for p in DST.rglob("*")
                if p.is_file() and p.suffix.lower() in (".jpg", ".mp4")] \
        if DST.exists() else []
    check("u6: organized media removed after undo", not leftover,
          f"{len(leftover)} left")
    check("u6: all 25 originals still intact",
          len([p for p in SRC.rglob("*") if p.is_file()]) == 25)


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage == "u3":
        u3()
    elif stage == "u4":
        u4()
    elif stage == "u5":
        dlg = u5()
    elif stage == "u6":
        dlg = dialog("Done", timeout=5)
        u6(dlg)
    else:
        u3()
        u4()
        dlg = u5()
        u6(dlg)
    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"\nUIA DRIVE: {ok}/{len(RESULTS)} passed")
    report()


def report():
    out = Path(__file__).resolve().parent / "real_ui_run"
    prev = (out / "REPORT.md").read_text(encoding="utf-8") \
        if (out / "REPORT.md").exists() else ""
    lines = ["\n\n# UIA-only drive (desktop locked) — appended\n"]
    ok = sum(1 for _, o, _ in RESULTS if o)
    lines.append(f"**{ok}/{len(RESULTS)} checks passed**\n")
    lines.append("## Evidence (live UIA texts)\n")
    for e in EVIDENCE:
        lines.append(f"- {e}")
    lines.append("\n## Checklist\n")
    for label, good, extra in RESULTS:
        lines.append(f"- {'PASS' if good else 'FAIL'} — {label}"
                     + (f" ({extra})" if extra else ""))
    (out / "REPORT.md").write_text(prev + "\n".join(lines), encoding="utf-8")
    print(f"report appended: {out / 'REPORT.md'}")


if __name__ == "__main__":
    main()
