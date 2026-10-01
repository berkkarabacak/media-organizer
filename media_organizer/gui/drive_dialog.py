"""Sign in, choose one Drive folder, and upload the organized library.

Upload is one-way and additive. It copies the organized tree into the
bound folder. It does not delete local files or remote files.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from .. import APP_NAME
from ..core.drive_folders import (
    DriveFolder,
    DriveFolderError,
    commit_folder_choice,
    commit_listed_folder,
    folder_bind_effect,
)
from ..core.display import format_bytes
from ..core.drive_sync import SyncRecordError, load_sync_record, save_sync_record
from ..core.drive_upload import UploadResult
from ..core.google_auth import GoogleSession, public_error_message
from .workers import DriveUploadWorker

REPLACE_FOLDER_TEXT = (
    "This library is already linked to a different Drive folder.\n\n"
    "Linking a new folder forgets the file ids stored for the old one, "
    "so a later upload would send those files again. "
    "Files already in the old Drive folder stay there.\n\n"
    "Link the new folder?"
)

_FOLDER_NAME_PREFIX = "driveFolderName/"


class _CallWorker(QThread):
    """Run one session call off the UI thread. The result is not a token."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except Exception as exc:
            self.failed.emit(public_error_message(exc))
            return
        self.succeeded.emit(result)


class FolderListDialog(QDialog):
    """Pick one folder from the Drive list, or browse with Google's picker."""

    def __init__(self, folders: list[DriveFolder], browse=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose a Drive folder")
        self.setMinimumWidth(440)
        self.folders = list(folders)
        self._browse = browse
        self.chosen: DriveFolder | None = None
        self.from_browser = False
        self._worker: _CallWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)
        title = QLabel("Choose a Drive folder")
        title.setObjectName("cardQuestion")
        layout.addWidget(title)
        hint = QLabel(
            "The organized library will be linked to this folder. "
            "Nothing is uploaded."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.folder_list = QListWidget()
        self.folder_list.setObjectName("driveFolderList")
        self.folder_list.setMinimumHeight(220)
        for folder in self.folders:
            item = QListWidgetItem(folder.name)
            item.setData(Qt.UserRole, folder.id)
            self.folder_list.addItem(item)
        self.folder_list.itemDoubleClicked.connect(lambda _item: self._use_selected())
        self.folder_list.currentRowChanged.connect(lambda _row: self._sync_use_button())
        layout.addWidget(self.folder_list)

        self.empty_label = QLabel(
            "No Drive folders are visible to this app yet. "
            "Browse Google Drive to choose one folder. "
            "That does not upload anything."
        )
        self.empty_label.setObjectName("muted")
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(not self.folders)
        layout.addWidget(self.empty_label)

        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.use_btn = QPushButton("Use this folder")
        self.use_btn.setEnabled(False)
        self.use_btn.clicked.connect(self._use_selected)
        buttons.addWidget(self.use_btn)
        self.browse_btn = QPushButton("Browse Google Drive…")
        self.browse_btn.clicked.connect(self._on_browse)
        self.browse_btn.setVisible(browse is not None)
        if browse is not None and not self.folders:
            self.browse_btn.setObjectName("primaryButton")
            self.browse_btn.style().unpolish(self.browse_btn)
            self.browse_btn.style().polish(self.browse_btn)
        buttons.addWidget(self.browse_btn)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _sync_use_button(self):
        self.use_btn.setEnabled(self.folder_list.currentRow() >= 0)

    def _current_folder(self) -> DriveFolder | None:
        item = self.folder_list.currentItem()
        if item is None:
            return None
        folder_id = item.data(Qt.UserRole)
        for folder in self.folders:
            if folder.id == folder_id:
                return folder
        return None

    def _use_selected(self):
        folder = self._current_folder()
        if folder is None:
            return
        self.chosen = folder
        self.from_browser = False
        self.accept()

    def _on_browse(self):
        if self._browse is None or self._worker_running():
            return
        self._set_busy(True, "Waiting for the browser…")
        self._worker = _CallWorker(self._browse, self)
        self._worker.succeeded.connect(self._on_browsed)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_browsed(self, folder: DriveFolder):
        self._set_busy(False, "")
        if not isinstance(folder, DriveFolder):
            return
        self.chosen = folder
        self.from_browser = True
        self.accept()

    def _on_failed(self, message: str):
        self._set_busy(False, "")
        QMessageBox.warning(self, APP_NAME, message)

    def _worker_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _set_busy(self, busy: bool, message: str):
        self.use_btn.setEnabled(not busy and self.folder_list.currentRow() >= 0)
        self.browse_btn.setEnabled(not busy)
        self.status_label.setText(message)

    def closeEvent(self, event):
        if self._worker_running():
            event.ignore()
            return
        super().closeEvent(event)


class DriveAccountDialog(QDialog):
    """Sign in, sign out, choose one Drive folder, and upload the library."""

    def __init__(self, library_dir: str = "", session=None, parent=None, uploader=None):
        super().__init__(parent)
        self.setWindowTitle("Google Drive")
        self.setMinimumWidth(520)
        self.session = session or GoogleSession()
        self._uploader = uploader
        self._worker: _CallWorker | None = None
        self._upload_worker: DriveUploadWorker | None = None
        self._call_active = False
        self._uploading = False
        self._settings = QSettings("MediaOrganizer", "MediaOrganizer")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        title = QLabel("Google Drive")
        title.setObjectName("cardQuestion")
        layout.addWidget(title)
        subtitle = QLabel(
            "Sign in, choose one folder, then upload the organized library."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        account_caption = QLabel("Account")
        account_caption.setObjectName("sectionLabel")
        layout.addWidget(account_caption)
        self.account_label = QLabel("Not signed in")
        self.account_label.setObjectName("driveAccountLabel")
        self.account_label.setWordWrap(True)
        layout.addWidget(self.account_label)

        account_buttons = QHBoxLayout()
        self.sign_in_btn = QPushButton("Sign in")
        self.sign_in_btn.setObjectName("primaryButton")
        self.sign_in_btn.clicked.connect(self._on_sign_in)
        self.sign_out_btn = QPushButton("Sign out")
        self.sign_out_btn.clicked.connect(self._on_sign_out)
        account_buttons.addWidget(self.sign_in_btn)
        account_buttons.addWidget(self.sign_out_btn)
        account_buttons.addStretch(1)
        layout.addLayout(account_buttons)

        folder_caption = QLabel("Drive folder")
        folder_caption.setObjectName("sectionLabel")
        layout.addWidget(folder_caption)
        self.folder_label = QLabel("No folder chosen")
        self.folder_label.setObjectName("driveFolderLabel")
        self.folder_label.setWordWrap(True)
        layout.addWidget(self.folder_label)
        self.folder_id_label = QLabel("")
        self.folder_id_label.setObjectName("muted")
        self.folder_id_label.setWordWrap(True)
        self.folder_id_label.setVisible(False)
        layout.addWidget(self.folder_id_label)

        library_caption = QLabel("Organized library")
        library_caption.setObjectName("sectionLabel")
        layout.addWidget(library_caption)
        library_row = QHBoxLayout()
        self.library_edit = QLineEdit(library_dir)
        self.library_edit.setReadOnly(True)
        self.library_edit.setPlaceholderText("Choose the destination folder from step 1")
        self.library_btn = QPushButton("Browse…")
        self.library_btn.clicked.connect(self._browse_library)
        library_row.addWidget(self.library_edit, 1)
        library_row.addWidget(self.library_btn)
        layout.addLayout(library_row)

        actions = QHBoxLayout()
        self.choose_btn = QPushButton("Choose Drive folder")
        self.choose_btn.clicked.connect(self._on_choose)
        self.upload_btn = QPushButton("Upload")
        self.upload_btn.setObjectName("driveUploadButton")
        self.upload_btn.clicked.connect(self._on_upload)
        actions.addWidget(self.choose_btn)
        actions.addWidget(self.upload_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        note = QLabel(
            "Organize stays on this computer. Upload copies the organized "
            "folders into the Drive folder you chose. A later upload skips "
            "files that have not changed and sends only new or changed ones. "
            "Files already in Drive stay where they are. Nothing on this "
            "computer is deleted."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        close_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_box.rejected.connect(self.reject)
        close_box.accepted.connect(self.accept)
        layout.addWidget(close_box)

        self._refresh_view()

    def _on_sign_in(self):
        self._start(self.session.sign_in, self._after_sign_in, "Waiting for the browser…")

    def _after_sign_in(self, _account):
        self._refresh_view()

    def _on_sign_out(self):
        if self._call_active or self._uploading or self._upload_thread_running():
            return
        try:
            self.session.sign_out()
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, public_error_message(exc))
            return
        self._refresh_view()

    def _on_choose(self):
        if self.session.signed_in_account() is None:
            return
        if not self._library_ready():
            return
        self._start(self.session.list_folders, self._show_folder_list, "Looking up Drive folders…")

    def _show_folder_list(self, folders):
        if not isinstance(folders, list):
            return
        dialog = FolderListDialog(
            folders, browse=self.session.pick_drive_folder, parent=self,
        )
        if dialog.exec() != QDialog.Accepted or dialog.chosen is None:
            return
        self._apply_folder(
            dialog.chosen, from_browser=dialog.from_browser, folders=folders,
        )

    def _browse_library(self):
        current = self.library_edit.text().strip()
        start = current if current and Path(current).is_dir() else ""
        path = QFileDialog.getExistingDirectory(self, "Choose the organized library", start)
        if not path:
            return
        self.library_edit.setText(path)
        self._show_bound_folder()

    def _apply_folder(
        self,
        folder: DriveFolder,
        *,
        from_browser: bool,
        folders: list[DriveFolder],
        confirm=None,
    ) -> bool:
        """Store ``folder.id`` on the library sync record. Asks before replacing."""
        library = self._library_path()
        if library is None:
            self._warn(
                "Choose the organized library first. That is the destination "
                "folder from the first step. The Drive folder is remembered "
                "with that library."
            )
            return False
        try:
            record = load_sync_record(library)
        except SyncRecordError:
            self._warn(
                "The Drive record in this library could not be read, so the "
                "folder was not changed."
            )
            return False
        try:
            effect = folder_bind_effect(record, folder.id)
        except ValueError:
            self._warn("That Drive folder could not be used.")
            return False
        replace_ok = True
        if effect == "replace":
            replace_ok = confirm() if confirm is not None else self._ask_replace()
        try:
            if from_browser:
                outcome = commit_folder_choice(
                    record, folder.id, replace_confirmed=replace_ok,
                )
            else:
                outcome = commit_listed_folder(
                    record, folders, folder.id, replace_confirmed=replace_ok,
                )
        except (DriveFolderError, ValueError) as exc:
            self._warn(str(exc))
            return False
        if outcome == "declined":
            return False
        if outcome != "unchanged":
            try:
                save_sync_record(record, library)
            except OSError:
                self._warn("The Drive folder could not be saved with this library.")
                return False
        if folder.name and folder.name != folder.id:
            self._remember_name(folder.id, folder.name)
        self._show_bound_folder()
        return True

    def _ask_replace(self) -> bool:
        answer = QMessageBox.question(
            self, APP_NAME, REPLACE_FOLDER_TEXT,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _library_ready(self) -> bool:
        if self._library_path() is not None:
            return True
        self._warn(
            "Choose the organized library first. That is the destination "
            "folder from the first step. The Drive folder is remembered "
            "with that library."
        )
        return False

    def _library_path(self) -> Path | None:
        text = self.library_edit.text().strip()
        if not text:
            return None
        path = Path(text)
        if not path.is_dir():
            return None
        return path

    def _on_upload(self):
        if self._upload_thread_running() or self._uploading:
            if self._upload_worker is not None:
                self._upload_worker.cancel()
            self.upload_btn.setEnabled(False)
            self.status_label.setText(
                "Stopping… Files already sent will be skipped next time."
            )
            return
        if self._call_busy():
            return
        try:
            signed_in = self.session.signed_in_account() is not None
        except Exception:
            signed_in = False
        if not signed_in:
            return
        library = self._library_path()
        if library is None or not self._bound_folder_id():
            return
        self._uploading = True
        self._upload_worker = DriveUploadWorker(
            str(library),
            self.session.access_token,
            uploader=self._uploader,
            parent=self,
        )
        self._upload_worker.progress.connect(self._on_upload_progress)
        self._upload_worker.finished_upload.connect(self._on_upload_finished)
        self._upload_worker.failed.connect(self._on_upload_failed)
        self._upload_worker.finished.connect(self._refresh_controls)
        self._upload_worker.start()
        self.status_label.setText("Starting upload…")
        self._refresh_controls()

    def _on_upload_progress(self, update):
        name = update.current_file or "the library"
        verb = "Uploading" if update.uploading else "Checking"
        skipped = getattr(update, "skipped", 0)
        uploaded = getattr(update, "uploaded", 0)
        self.status_label.setText(
            f"{verb} {name} — {update.files_done} of {update.files_total} files, "
            f"{skipped} unchanged, {uploaded} sent, "
            f"{format_bytes(update.bytes_done)} of {format_bytes(update.bytes_total)} to send"
        )

    def _on_upload_finished(self, result):
        self._uploading = False
        self._refresh_controls()
        if not isinstance(result, UploadResult):
            self.status_label.setText("Upload finished.")
            return
        if result.cancelled:
            self.status_label.setText(
                "Upload stopped. Files already sent will be skipped next time. "
                "The file that was in progress can continue when you upload again."
            )
            return
        if result.files_total == 0:
            self.status_label.setText("There are no files to upload in this library.")
            return
        sent = format_bytes(getattr(result, "bytes_sent", 0))
        self.status_label.setText(
            f"Upload finished. {result.uploaded} sent ({sent}), "
            f"{result.skipped} unchanged."
        )

    def _on_upload_failed(self, message: str):
        self._uploading = False
        self._refresh_controls()
        text = (message or "").strip() or "Upload did not finish. Try again."
        self.status_label.setText(text)
        QMessageBox.warning(self, APP_NAME, text)

    def _refresh_view(self):
        account = None
        try:
            account = self.session.signed_in_account()
        except Exception:
            account = None
        self.account_label.setText(account.label if account is not None else "Not signed in")
        self._show_bound_folder()

    def _show_bound_folder(self):
        library = self._library_path()
        if library is None:
            self._set_folder_text("", "")
            if self.library_edit.text().strip():
                self.folder_label.setText("Choose an existing library folder")
            self._refresh_controls()
            return
        try:
            record = load_sync_record(library)
        except SyncRecordError:
            self.folder_label.setText("The Drive record in this library could not be read.")
            self.folder_id_label.setVisible(False)
            self._refresh_controls()
            return
        name = self._remembered_name(record.drive_folder_id)
        self._set_folder_text(record.drive_folder_id, name)
        self._refresh_controls()

    def _set_folder_text(self, folder_id: str, name: str):
        if not folder_id:
            self.folder_label.setText("No folder chosen")
            self.folder_id_label.setText("")
            self.folder_id_label.setVisible(False)
            return
        display = name or folder_id
        self.folder_label.setText(display)
        if name and name != folder_id:
            self.folder_id_label.setText(folder_id)
            self.folder_id_label.setVisible(True)
        else:
            self.folder_id_label.setText("")
            self.folder_id_label.setVisible(False)

    def _remember_name(self, folder_id: str, name: str) -> None:
        safe = folder_id.replace("/", "_")
        self._settings.setValue(_FOLDER_NAME_PREFIX + safe, name)

    def _remembered_name(self, folder_id: str) -> str:
        if not folder_id:
            return ""
        safe = folder_id.replace("/", "_")
        value = self._settings.value(_FOLDER_NAME_PREFIX + safe, "")
        return value if isinstance(value, str) else ""

    def _start(self, fn, on_ok, waiting: str):
        if self._call_active or self._worker_running():
            return
        self._call_active = True
        self._set_busy(True, waiting)
        self._worker = _CallWorker(fn, self)
        self._worker.succeeded.connect(lambda result: self._finish_ok(on_ok, result))
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _finish_ok(self, on_ok, result):
        self._call_active = False
        self._set_busy(False, "")
        on_ok(result)

    def _on_failed(self, message: str):
        self._call_active = False
        self._set_busy(False, "")
        QMessageBox.warning(self, APP_NAME, message)

    def _set_busy(self, busy: bool, message: str):
        self.status_label.setText(message)
        self._refresh_controls()

    def _refresh_controls(self):
        signed_in = False
        try:
            signed_in = self.session.signed_in_account() is not None
        except Exception:
            signed_in = False
        call_busy = self._call_busy()
        upload_busy = self._uploading or self._upload_thread_running()
        self.sign_in_btn.setEnabled(not signed_in and not call_busy and not upload_busy)
        self.sign_out_btn.setEnabled(signed_in and not call_busy and not upload_busy)
        self.choose_btn.setEnabled(signed_in and not call_busy and not upload_busy)
        self.library_btn.setEnabled(not call_busy and not upload_busy)
        if upload_busy:
            self.upload_btn.setText("Cancel")
            self.upload_btn.setEnabled(self._uploading)
            self.upload_btn.setToolTip(
                "Stop before the next file. Files already uploaded stay on Drive "
                "and are skipped next time."
            )
        else:
            self.upload_btn.setText("Upload")
            self.upload_btn.setEnabled(
                signed_in and not call_busy and bool(self._bound_folder_id())
            )
            self.upload_btn.setToolTip(
                "Copy this organized library into the chosen Drive folder. "
                "Unchanged files are skipped. A new file is added, and a "
                "changed file updates the copy already in this folder. "
                "Files on this computer stay here. Nothing already in Drive is deleted."
            )
        self._restyle(signed_in, upload_busy)

    def _bound_folder_id(self) -> str:
        library = self._library_path()
        if library is None:
            return ""
        try:
            return load_sync_record(library).drive_folder_id
        except SyncRecordError:
            return ""

    def _call_busy(self) -> bool:
        return self._call_active

    def _upload_thread_running(self) -> bool:
        return self._upload_worker is not None and self._upload_worker.isRunning()

    def _restyle(self, signed_in: bool, upload_busy: bool = False):
        """The amber button is the next step: Sign in, then folder, then Upload."""
        can_upload = signed_in and bool(self._bound_folder_id()) and not upload_busy
        self._set_primary(self.sign_in_btn, not signed_in and not upload_busy)
        self._set_primary(self.choose_btn, signed_in and not can_upload and not upload_busy)
        self._set_primary(self.upload_btn, can_upload)

    def _set_primary(self, button: QPushButton, primary: bool):
        button.setObjectName("primaryButton" if primary else "")
        button.style().unpolish(button)
        button.style().polish(button)

    def _worker_running(self) -> bool:
        running = self._worker is not None and self._worker.isRunning()
        return self._call_active or running or self._upload_thread_running()

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, APP_NAME, text)

    def closeEvent(self, event):
        if self._call_busy() or self._upload_thread_running():
            event.ignore()
            return
        super().closeEvent(event)
