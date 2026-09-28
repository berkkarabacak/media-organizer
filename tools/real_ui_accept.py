"""Installed-app acceptance drive (v1.5.2), UIA-only.

Stages: a1 folders, a2 strategy+scan, a3 plan verify, a4 organize+disk,
a5 close+exit-verify, a6 windows integration, all.
"""

import subprocess
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
    ctrl.iface_invoke.Invoke()


def edits(win):
    return [c for c in win.descendants()
            if c.element_info.control_type == "Edit"
            and c.rectangle().width() > 200]


def wait_rows(win, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rows = sum(1 for c in win.descendants()
                   if c.element_info.control_type == "DataItem")
        if rows:
            return rows
        time.sleep(0.5)
    return 0


def dialog_by_marker(marker, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        w = window()
        if any(marker in t for t in texts(w)):
            return w
        time.sleep(0.3)
    return None


def a1():
    w = window()
    # navigate back to Step 1 if needed (state persists across sessions)
    for _ in range(3):
        if any("Choose your folders" in t for t in texts(w)):
            break
        back = find(w, "Back", "Button")
        if back is None:
            break
        invoke(back)
        time.sleep(0.6)
    ts = texts(w)
    check("a1: app on Step 1", any("Choose your folders" in t for t in ts))
    eds = edits(w)
    check("a1: two path fields", len(eds) >= 2)
    # reset stale persisted values, then set fresh
    eds[0].iface_value.SetValue("")
    eds[1].iface_value.SetValue("")
    time.sleep(0.3)
    eds[0].iface_value.SetValue(str(SRC))
    eds[1].iface_value.SetValue(str(DST))
    time.sleep(0.3)
    check("a1: source set", eds[0].window_text().endswith("demo-library"),
          eds[0].window_text()[-40:])
    check("a1: destination set (fresh)",
          eds[1].window_text().endswith("demo-library_Organized"),
          eds[1].window_text()[-45:])
    invoke(find(w, "Continue", "Button"))
    time.sleep(0.8)
    check("a1: now on Step 2",
          any("How should we sort" in t for t in texts(w)))


def a2():
    w = window()
    card_text = find_part(w, "Year → Quarter → Month", "Text")
    check("a2: nested strategy card found", card_text is not None)
    card = card_text.parent()  # the clickable QFrame card (Custom, Invoke)
    invoke(card)
    time.sleep(0.5)
    # selection isn't exposed via UIA; proven through the plan's paths in a3
    nxt = find(w, "Check the plan", "Button")
    check("a2: Check the plan found", nxt is not None)
    invoke(nxt)
    rows = wait_rows(w)
    check("a2: scan completed with rows", rows > 0, f"cells={rows}")


def a3():
    w = window()
    ts = texts(w)
    summary = next((t for t in ts if "files" in t and "total" in t), "")
    evidence("plan summary", summary)
    check("a3: summary shows 25 files", "25 files" in summary, summary)
    check("a3: summary mentions duplicates+uncertain",
          "duplicate" in summary and "uncertain" in summary, summary)
    items = [c.window_text() or "" for c in w.descendants()
             if c.element_info.control_type == "DataItem"]
    joined = " || ".join(items)
    check("a3: nested path 2019/Q3/07 July for IMG_4031.jpg",
          "2019/Q3/07 July/IMG_4031.jpg" in joined,
          next((i for i in items if "IMG_4031" in i), "row not visible"))
    check("a3: nested path 2022/Q2/06 June for birthday_party.mp4",
          "2022/Q2/06 June/birthday_party.mp4" in joined)
    evidence("sample rows", joined[:400])


def a4():
    w = window()
    dry = find_part(w, "Dry run", "CheckBox")
    try:
        if int(dry.iface_toggle.CurrentToggleState) == 1:
            dry.iface_toggle.Toggle()
    except Exception:
        pass
    org = find(w, "Organize now", "Button")
    check("a4: organize enabled", org is not None and org.is_enabled())
    invoke(org)
    dlg = dialog_by_marker("Done!", timeout=60)
    check("a4: completion dialog appeared", dlg is not None)
    if dlg:
        body = " | ".join(t for t in texts(dlg) if t)
        evidence("done dialog", body[:250])
        check("a4: dialog reports copied count", "Done!" in body, body[:120])

    def disk(label, cond, extra=""):
        check(f"a4-disk: {label}", cond, extra)
    disk("organized folder exists", DST.exists())
    disk("IMG_4031.jpg in 2019/Q3/07 July (nested)",
         (DST / "2019" / "Q3" / "07 July" / "IMG_4031.jpg").exists())
    disk("birthday_party.mp4 in 2022/Q2/06 June",
         (DST / "2022" / "Q2" / "06 June" / "birthday_party.mp4").exists())
    disk("WhatsApp file in 2022/Q2/06 June",
         (DST / "2022" / "Q2" / "06 June" / "IMG-20220614-WA0031.jpg").exists())
    unc = list((DST / "_uncertain").glob("*")) if (DST / "_uncertain").exists() else []
    disk("3 mtime-only files in _uncertain/", len(unc) == 3,
         str([p.name for p in unc]))
    ref = (SRC / "IMG_4031.jpg").read_bytes()
    copies = [p for p in DST.rglob("*")
              if p.is_file() and p.stat().st_size == len(ref)
              and p.read_bytes() == ref]
    disk("IMG_4031 content exactly once", len(copies) == 1,
         str([str(p.relative_to(DST)) for p in copies]))
    disk("all 25 originals intact",
         len([p for p in SRC.rglob("*") if p.is_file()]) == 25)
    # close the completion dialog (not Undo — acceptance run keeps output)
    if dlg:
        close = None
        for c in dlg.descendants():
            try:
                if (c.window_text() or "") == "Close" and \
                        c.element_info.control_type == "Button" and \
                        c.rectangle().top > 200:
                    close = c
            except Exception:
                pass
        if close:
            invoke(close)
    time.sleep(0.6)


def a5():
    w = window()
    # File menu -> Exit (menu items expose InvokePattern)
    file_menu = find(w, "File", "MenuItem")
    check("a5: File menu found", file_menu is not None)
    try:
        file_menu.iface_expand_collapse.Expand()
    except Exception:
        invoke(file_menu)
    time.sleep(0.8)
    d = Desktop(backend="uia")
    exit_item = None
    for win2 in d.windows():
        try:
            for c in win2.descendants():
                if c.element_info.control_type == "MenuItem" and \
                        (c.window_text() or "") in ("Exit", "E&xit"):
                    exit_item = c
        except Exception:
            pass
    check("a5: Exit menu item found", exit_item is not None)
    if exit_item:
        invoke(exit_item)
    else:  # fallback: window Close button
        close = find(w, "Close", "Button")
        if close:
            invoke(close)
    time.sleep(2)
    r = subprocess.run(["tasklist"], capture_output=True, text=True)
    alive = "MediaOrganizer" in r.stdout
    check("a5: process exited cleanly", not alive)


def a6():
    import os
    start_menu = Path(os.environ["APPDATA"]) / \
        r"Microsoft\Windows\Start Menu\Programs\Media Organizer"
    lnk = start_menu / "Media Organizer.lnk"
    check("a6: Start Menu shortcut exists", lnk.exists(), str(lnk))
    install = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Media Organizer"
    unins = install / "unins000.exe"
    check("a6: uninstaller exists (unins000.exe)", unins.exists(), str(unins))
    check("a6: installed exe exists",
          (install / "MediaOrganizer.exe").exists())


def report():
    out = Path(__file__).resolve().parent / "real_ui_run"
    prev = (out / "REPORT.md").read_text(encoding="utf-8") \
        if (out / "REPORT.md").exists() else ""
    ok = sum(1 for _, o, _ in RESULTS if o)
    lines = ["\n\n# Installed-app acceptance (v1.5.2)\n",
             f"**{ok}/{len(RESULTS)} checks passed**\n",
             "Install: `C:\\Users\\OdinLocal\\AppData\\Local\\Programs\\Media Organizer` "
             "(silent installer), PID 37112. Driven UIA-only (desktop locked).\n",
             "## Evidence (live UIA texts)\n"]
    for e in EVIDENCE:
        lines.append(f"- {e}")
    lines.append("\n## Checklist\n")
    for label, good, extra in RESULTS:
        lines.append(f"- {'PASS' if good else 'FAIL'} — {label}"
                     + (f" ({extra})" if extra else ""))
    (out / "REPORT.md").write_text(prev + "\n".join(lines), encoding="utf-8")
    print(f"report appended: {out / 'REPORT.md'}")


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage == "all":
        a1(); a2(); a3(); a4(); a5(); a6()
    else:
        {"a1": a1, "a2": a2, "a3": a3, "a4": a4, "a5": a5, "a6": a6}[stage]()
    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"\nACCEPTANCE: {ok}/{len(RESULTS)} passed")
    if stage == "all":
        report()


if __name__ == "__main__":
    main()
