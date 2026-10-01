"""Background workers (QThread) for scanning, organizing, and Drive upload."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.drive_upload import upload_library
from ..core.duplicates import find_duplicates
from ..core.executor import execute_plan
from ..core.google_auth import public_error_message
from ..core.metadata import analyze_media_batch
from ..core.organizer import (OrganizeOptions, PlannedFile, build_plan,
                              scan_media_files)
from ..core.strategies import get_strategy

#: Minimum interval between progress signal emissions (keeps the UI smooth).
PROGRESS_INTERVAL_S = 0.15


class ScanWorker(QThread):
    """Builds the organise plan off the UI thread.

    Emits determinate progress: a fast pre-count pass establishes the total,
    then hashing (if duplicate-skip is on) + planning report files done.
    """

    # done, total, current_name — total == 0 means "counting, indeterminate"
    progress = Signal(int, int, str)
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

            last_emit = [0.0]

            def emit(done, total, name, force=False):
                now = time.monotonic()
                if not force and now - last_emit[0] < PROGRESS_INTERVAL_S:
                    return
                last_emit[0] = now
                self.progress.emit(done, total, name)

            # Phase 0: list files (the walk doubles as the pre-count)
            self.progress.emit(0, 0, "Counting files…")
            files = list(scan_media_files(self.options))
            if is_cancelled():
                self.finished_plan.emit([])
                return
            n = len(files)
            phases = (1 if self.options.skip_duplicates else 0) + 2
            grand_total = max(n * phases, 1)
            offset = 0

            # Phase 1: duplicate hashing (parallel inside find_duplicates)
            duplicates = set()
            if self.options.skip_duplicates:
                duplicates = find_duplicates(
                    files,
                    progress=lambda i, _n: emit(i, grand_total, _n),
                    cancel=is_cancelled,
                )
                offset += n

            # Phase 2: metadata extraction (parallel thread pool)
            need_gps = get_strategy(self.options.strategy).uses_location
            analysis = analyze_media_batch(
                files, need_gps=need_gps,
                progress=lambda i, _n: emit(offset + i, grand_total, _n),
                cancel=is_cancelled,
            )
            offset += n

            # Phase 3: plan assembly (pure, fast)
            plan = build_plan(
                self.options,
                duplicates=duplicates,
                files=files,
                analysis=analysis,
                progress=lambda i, _n: emit(offset + i, grand_total, _n),
                cancel=is_cancelled,
            )
            emit(grand_total, grand_total, "", force=True)
            self.finished_plan.emit(plan)
        except Exception as exc:  # never crash the UI
            self.failed.emit(str(exc))


class OrganizeWorker(QThread):
    """Executes the plan off the UI thread.

    Byte counts are emitted as `object` (Python ints) on purpose: Qt `int`
    is 32-bit signed, and the byte total of any real photo library exceeds
    2^31, which made slot delivery raise OverflowError and silently killed
    all progress updates.
    """

    # i, total, name, bytes_done, total_bytes
    progress = Signal(int, int, str, object, object)
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
            last_emit = [0.0]

            def on_progress(i, total, name, bytes_done, total_bytes):
                now = time.monotonic()
                is_last = (i >= total)
                if not is_last and now - last_emit[0] < PROGRESS_INTERVAL_S:
                    return  # throttle: UI updates ~7x per second are enough
                last_emit[0] = now
                self.progress.emit(i, total, name, bytes_done, total_bytes)

            log, summary = execute_plan(
                self.plan,
                self.options,
                progress=on_progress,
                cancel=lambda: self._cancelled,
            )
            self.finished_run.emit(log, summary)
        except Exception as exc:
            self.failed.emit(str(exc))


class DriveUploadWorker(QThread):
    """Uploads one organized library off the UI thread.

    ``progress`` carries an ``UploadProgress``. Byte counts stay Python ints
    so a large library cannot overflow Qt's 32-bit int. ``cancel`` is
    cooperative: the upload stops before the next file and leaves the
    in-flight resumable session stored beside the OAuth token.
    """

    progress = Signal(object)
    finished_upload = Signal(object)
    failed = Signal(str)

    def __init__(self, library_dir, access_token, *, uploader=None, parent=None):
        super().__init__(parent)
        self.library_dir = library_dir
        self._access_token = access_token
        self._uploader = uploader or upload_library
        self._cancelled = False
        self._last_emit = 0.0
        self._last_name = None
        self._last_done = -1

    def cancel(self):
        self._cancelled = True

    def _emit(self, update) -> None:
        now = time.monotonic()
        changed = (
            update.current_file != self._last_name
            or update.files_done != self._last_done
        )
        finished = (
            update.files_total > 0
            and update.files_done >= update.files_total
            and not update.uploading
        )
        if not changed and not finished and now - self._last_emit < PROGRESS_INTERVAL_S:
            return
        self._last_emit = now
        self._last_name = update.current_file
        self._last_done = update.files_done
        self.progress.emit(update)

    def run(self):
        try:
            result = self._uploader(
                self.library_dir,
                access_token=self._access_token,
                progress=self._emit,
                cancel=lambda: self._cancelled,
            )
            self.finished_upload.emit(result)
        except Exception as exc:
            self.failed.emit(public_error_message(exc))
