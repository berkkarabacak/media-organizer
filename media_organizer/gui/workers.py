"""Background workers (QThread) for scanning and executing."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.duplicates import find_duplicates
from ..core.executor import execute_plan
from ..core.organizer import OrganizeOptions, PlannedFile, build_plan, scan_media_files


class ScanWorker(QThread):
    """Builds the organise plan off the UI thread."""

    progress = Signal(int, str)
    finished_plan = Signal(list)          # list[PlannedFile]
    failed = Signal(str)

    def __init__(self, options: OrganizeOptions, parent=None):
        super().__init__(parent)
        self.options = options
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            def is_cancelled():
                return self._cancelled

            duplicates = set()
            if self.options.skip_duplicates:
                files = list(scan_media_files(self.options))
                duplicates = find_duplicates(
                    files,
                    progress=lambda i, n: self.progress.emit(i, f"Hashing {n}"),
                    cancel=is_cancelled,
                )
            plan = build_plan(
                self.options,
                duplicates=duplicates,
                progress=lambda i, n: self.progress.emit(i, n),
                cancel=is_cancelled,
            )
            self.finished_plan.emit(plan)
        except Exception as exc:  # never crash the UI
            self.failed.emit(str(exc))


class OrganizeWorker(QThread):
    """Executes the plan off the UI thread."""

    progress = Signal(int, int, str)
    finished_run = Signal(object, dict)   # (RunLog, summary)
    failed = Signal(str)

    def __init__(self, plan: list[PlannedFile], options: OrganizeOptions, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.options = options
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            log, summary = execute_plan(
                self.plan,
                self.options,
                progress=lambda i, t, n: self.progress.emit(i, t, n),
                cancel=lambda: self._cancelled,
            )
            self.finished_run.emit(log, summary)
        except Exception as exc:
            self.failed.emit(str(exc))
