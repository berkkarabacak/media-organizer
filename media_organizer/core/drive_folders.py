"""List Drive folders and bind one of them on the local sync record.

This module does not upload files, create Drive files, or open an upload
session. Listing uses ``files.list``. Reading one chosen folder uses
``files.get``. Binding calls ``SyncRecord.bind_folder``.
"""

from __future__ import annotations

import urllib.parse

from .drive_sync import SyncRecord

FOLDER_MIME = "application/vnd.google-apps.folder"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
_LIST_QUERY = "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
_PAGE_LIMIT = 10


class DriveFolderError(Exception):
    """The folder choice cannot be used. Nothing was uploaded."""


class DriveFolder:
    """One Drive folder the user can bind. ``id`` is what the sync record stores."""

    def __init__(self, id: str, name: str):
        self.id = id
        self.name = name or id

    def __eq__(self, other: object) -> bool:
        return isinstance(other, DriveFolder) and self.id == other.id and self.name == other.name

    def __repr__(self) -> str:
        return f"DriveFolder(id={self.id!r}, name={self.name!r})"


def folder_list_url(page_token: str = "") -> str:
    """``files.list`` URL for folders. There is no upload parameter."""
    fields = {
        "q": _LIST_QUERY,
        "fields": "nextPageToken,files(id,name,mimeType)",
        "pageSize": "100",
        "orderBy": "name",
        "spaces": "drive",
    }
    if page_token:
        fields["pageToken"] = page_token
    return FILES_URL + "?" + urllib.parse.urlencode(fields)


def list_drive_folders(access_token: str, *, get_json, page_limit: int = _PAGE_LIMIT) -> list[DriveFolder]:
    """Return folders the app can already access. Does not create any."""
    folders: list[DriveFolder] = []
    seen_ids: set[str] = set()
    seen_pages: set[str] = set()
    page_token = ""
    for _ in range(page_limit):
        if page_token:
            if page_token in seen_pages:
                break
            seen_pages.add(page_token)
        payload = get_json(folder_list_url(page_token), access_token)
        if not isinstance(payload, dict):
            raise DriveFolderError("Google Drive returned something this app could not read.")
        files = payload.get("files") or []
        if not isinstance(files, list):
            raise DriveFolderError("Google Drive returned something this app could not read.")
        for item in files:
            folder = _folder_from_item(item)
            if folder is None or folder.id in seen_ids:
                continue
            seen_ids.add(folder.id)
            folders.append(folder)
        next_token = payload.get("nextPageToken") or ""
        if not isinstance(next_token, str) or not next_token:
            break
        page_token = next_token
    folders.sort(key=lambda folder: (folder.name.casefold(), folder.id))
    return folders


def fetch_drive_folder(access_token: str, folder_id: str, *, get_json) -> DriveFolder:
    """Read the name of one folder. Refuses a file that is not a folder."""
    folder_id = _require_folder_id(folder_id)
    quoted = urllib.parse.quote(folder_id, safe="")
    url = f"{FILES_URL}/{quoted}?fields=id,name,mimeType"
    payload = get_json(url, access_token)
    if not isinstance(payload, dict):
        raise DriveFolderError("Google Drive returned something this app could not read.")
    mime = payload.get("mimeType")
    if mime and mime != FOLDER_MIME:
        raise DriveFolderError("Choose a Drive folder, not a file.")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        name = folder_id
    return DriveFolder(id=folder_id, name=name.strip())


def parse_one_folder_id(raw: str) -> str:
    """The desktop Picker returns ``picked_file_ids``. This app binds one."""
    if not isinstance(raw, str):
        raise DriveFolderError("Choose one Drive folder.")
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) != 1:
        raise DriveFolderError("Choose one Drive folder.")
    try:
        return _require_folder_id(parts[0])
    except ValueError as exc:
        raise DriveFolderError("Choose one Drive folder.") from exc


def folder_bind_effect(record: SyncRecord, folder_id: str) -> str:
    """``bind``, ``unchanged``, or ``replace``. Does not modify ``record``."""
    folder_id = _require_folder_id(folder_id)
    current = record.drive_folder_id
    if current == folder_id:
        return "unchanged"
    if current:
        return "replace"
    return "bind"


def commit_folder_choice(record: SyncRecord, folder_id: str, *, replace_confirmed: bool) -> str:
    """Bind ``folder_id``, or return ``declined`` without changing the record.

    Returns ``bind``, ``unchanged``, ``replaced``, or ``declined``.
    ``replaced`` drops stored file ids via ``SyncRecord.bind_folder``.
    Remote files are not deleted.
    """
    effect = folder_bind_effect(record, folder_id)
    if effect == "replace" and not replace_confirmed:
        return "declined"
    if effect == "unchanged":
        return "unchanged"
    record.bind_folder(folder_id)
    if effect == "replace":
        return "replaced"
    return "bind"


def commit_listed_folder(
    record: SyncRecord,
    folders: list[DriveFolder],
    folder_id: str,
    *,
    replace_confirmed: bool,
) -> str:
    """Bind a folder that was in the Drive list. Same confirm rules."""
    folder_id = _require_folder_id(folder_id)
    if folder_id not in {folder.id for folder in folders}:
        raise DriveFolderError("Choose a folder from the Drive list.")
    return commit_folder_choice(record, folder_id, replace_confirmed=replace_confirmed)


def _folder_from_item(item: object) -> DriveFolder | None:
    if not isinstance(item, dict):
        return None
    mime = item.get("mimeType")
    if mime and mime != FOLDER_MIME:
        return None
    raw_id = item.get("id")
    if not isinstance(raw_id, str):
        return None
    try:
        folder_id = _require_folder_id(raw_id)
    except ValueError:
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        name = folder_id
    return DriveFolder(id=folder_id, name=name.strip())


def _require_folder_id(folder_id: str) -> str:
    if not isinstance(folder_id, str) or not folder_id or any(ch.isspace() for ch in folder_id):
        raise ValueError("drive_folder_id must be a non-empty single-line id")
    return folder_id
