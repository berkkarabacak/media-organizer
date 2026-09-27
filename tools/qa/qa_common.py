"""Shared QA harness: real MainWindow + workers, offscreen, PASS/FAIL tally."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

_results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, extra: str = ""):
    _results.append((label, bool(ok), extra))
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + (f"  ({extra})" if extra else ""), flush=True)


def section(title: str):
    print(f"\n=== {title} ===", flush=True)


def summary() -> int:
    failed = [r for r in _results if not r[1]]
    print(f"\nQA TALLY: {len(_results) - len(failed)}/{len(_results)} passed", flush=True)
    if failed:
        print("FAILURES:", flush=True)
        for label, _, extra in failed:
            print(f"  - {label} {extra}", flush=True)
        print("RESULT: DEFECTS FOUND", flush=True)
        return 1
    print("RESULT: ALL OK", flush=True)
    return 0


_app = None


def app() -> QApplication:
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
        QMessageBox.exec = lambda self: None        # never block on modals
        QMessageBox.clickedButton = lambda self: None
        # static dialogs create+exec their own box; answer them non-blockingly
        QMessageBox.warning = staticmethod(
            lambda *a, **k: QMessageBox.Ok)
        QMessageBox.information = staticmethod(
            lambda *a, **k: QMessageBox.Ok)
        QMessageBox.critical = staticmethod(
            lambda *a, **k: QMessageBox.Ok)
        QMessageBox.question = staticmethod(
            lambda *a, **k: QMessageBox.No)   # safe default: refuse risky ops
    return _app


def pump(seconds: float = 0.0):
    end = time.monotonic() + seconds
    while True:
        app().processEvents()
        if time.monotonic() >= end:
            return
        time.sleep(0.004)


def make_window():
    """Fresh MainWindow with clean settings. Caller should close it."""
    from media_organizer.gui.main_window import MainWindow
    QSettings("MediaOrganizer", "MediaOrganizer").clear()
    w = MainWindow()
    w.show()
    pump(0.01)
    return w


def close_window(w):
    try:
        w.close()
        w.deleteLater()
        pump(0.01)
    except Exception:
        pass


def wait_worker(worker, timeout_s=60):
    """Pump the event loop while a worker runs; returns True when done."""
    end = time.monotonic() + timeout_s
    while worker is not None and worker.isRunning():
        if time.monotonic() > end:
            return False
        app().processEvents()
        time.sleep(0.005)
    worker.wait(1000)
    pump(0.02)
    return True


def fill(src, name: str, payload: bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 64) -> Path:
    """Create a file under src, making parents. Returns path (or None on OS refusal)."""
    p = src / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p
