# Google Drive incremental upload

Slice 1 of [issue 2](https://github.com/berkkarabacak/media-organizer/issues/2).
Later slices should follow this document.

The code in this slice is `media_organizer.core.drive_sync`. It loads and
saves a local sync record and decides whether a file is `skip` or `upload`.
It does not sign in, call the Drive API, show a folder picker, or upload.

## Where Drive sits

Organize stays local. Drive is a destination for a library that has already
been organized. The uploaded tree is the organized folders: year / quarter /
month, plus `_uncertain`, `_undated`, or `_unknown-location` when those
folders are in use.

This is one-way. Changes made in Drive are not written back to disk. A
finished upload leaves every local file in place.

## Sign-in

The sign-in slice uses Google OAuth for a desktop app: Authorization Code
with PKCE, and a loopback redirect on `127.0.0.1`.

Scope: `https://www.googleapis.com/auth/drive.file`. That covers files this
app creates and the one folder the user picks. It does not cover the rest
of the user's Drive.

Tokens stay on the machine.

- Directory: `%LOCALAPPDATA%\MediaOrganizer\google\` (the same per-user root
  as `crash.log`). When `LOCALAPPDATA` is unset, use
  `~/.local/share/MediaOrganizer/google/`.
- The token file is readable only by the user.
- Access tokens and refresh tokens stay in that directory. They are never
  written into the organized library, the sync record, a log, or this
  repository.
- Sign-out deletes that local token file.

Use a Desktop OAuth client with PKCE, so the app does not need a client
secret. Do not commit a client secret, a `client_secret.json`, a user token,
or a credential file. Those filenames are listed in `.gitignore`. This slice
does not add a client id. The sign-in slice adds one without adding a secret.

## One Drive folder

The user picks one Drive folder. Its id is stored as `drive_folder_id` on
the sync record. Files are uploaded under that folder, keeping the
library-relative path (`2024/Q3/07 July/IMG_1234.jpg`).

`SyncRecord.bind_folder` records the id. Binding a different id drops the
stored file ids so the next pass uploads into the new folder. The files
already in the previous folder stay there. Switching back has no memory of
the old ids, so those files would upload again, still without deleting
anything. The folder-picker slice should ask before replacing a folder that
already has a record.

## Sync record

Path: `<organized-library>/.media_organizer/drive_sync.json`, next to the
operation log. One record per organized library. The file holds Drive file
ids, not tokens. Copying the library does not copy the right to read Drive.

```json
{
  "version": 1,
  "mode": "additive",
  "drive_folder_id": "the-folder-the-user-picked",
  "entries": [
    {
      "local_path": "2024/Q3/07 July/IMG_1234.jpg",
      "size": 3482191,
      "content_hash": "64 lowercase hex characters from sha256",
      "drive_file_id": "the-id-drive-returned"
    }
  ]
}
```

- `local_path` is relative to the library root and uses `/`.
  `normalize_local_path` accepts `\` and `.` segments. It rejects absolute
  paths and `..`.
- `size` is the file size in bytes.
- `content_hash` is the SHA-256 hex digest from
  `media_organizer.core.duplicates.full_hash`.
- `drive_file_id` is non-empty only after Drive has accepted the file. An
  empty id means the upload did not finish.

`version` must be `1`. `mode` must be `additive`. A missing file loads as an
empty record, which is the first run. Unreadable JSON, another version,
another mode, a duplicate path, or a credential or upload-session field
raises `SyncRecordError`. The caller stops and tells the user. An unreadable
record is not an empty one: treating it as empty would upload the library
again and could place a second copy on Drive.

Unknown fields other than the forbidden list are kept across load and save.
The upload slice must still load, update, and save the same record. Saving a
fresh empty record would forget every file id.

These keys are refused anywhere in the file. The library folder is user data
that gets copied, so it must not carry a secret or a bearer upload session:
`token`, `access_token`, `refresh_token`, `id_token`, `client_secret`,
`client_secret_json`, `credentials`, `secret`, `resume_uri`,
`upload_session`, `session_uri`.

## What counts as changed

`SyncRecord.decide(local_path, size, content_hash)` returns `skip` or `upload`.

| Situation | Decision |
|---|---|
| No entry for that path | `upload` |
| Entry exists and `drive_file_id` is empty | `upload` |
| Same path, size differs | `upload` |
| Same path, content hash differs | `upload` |
| Same path, size and hash match, and a file id is set | `skip` |

The path is the identity. The same bytes at a new path are an upload. A
rename is a new path; the previous entry stays in the record.

When the decision is `upload` and `entry_for` still returns a Drive file id,
the upload slice updates that Drive file. It does not create a second file
for the same path. A path with no file id creates a new Drive file.

This decision uses the local record only. It does not ask Drive whether the
remote file is still there.

## Additive

`mode` is `additive`. This version never deletes a remote file:

- a local file that has been removed stays on Drive
- a renamed file is uploaded at the new path, and the old remote file stays
- a remote file this app did not place is left alone
- choosing a different Drive folder does not delete the previous folder

There is no delete decision and no delete list. A mirror mode that removes
remote files is a later, explicit choice. A record whose mode is not
`additive` is refused.

## Interrupted uploads

A file becomes skippable only after `mark_uploaded` and a successful
`save_sync_record`. `mark_uploaded` requires a bound folder and a Drive file
id.

Save writes `drive_sync.json.part`, fsyncs it, then replaces
`drive_sync.json`. A crash before the replace leaves the previous record.
Load ignores a leftover `.part` file. Files already recorded are skipped.
The file that was in flight is uploaded again, so a crash does not re-upload
the library.

The upload slice resumes the in-flight file's bytes on top of that rule:

- Use Drive's resumable upload for the single file in flight.
- Store the session URI next to the OAuth token, under
  `%LOCALAPPDATA%\MediaOrganizer\google\`, keyed by the library path and the
  local path. Do not store it in `drive_sync.json`.
- On restart, continue that session when the local size and hash are
  unchanged and the session is still valid. Otherwise start a new session.
- Call `mark_uploaded` only after Drive returns a file id, then save.
  `mark_uploaded` drops any in-memory session field (`resume_uri`,
  `pending_upload`, `upload_session`, `session_uri`) so a finished entry
  cannot be resumed.

## API

```text
load_sync_record(library_dir) -> SyncRecord
save_sync_record(record, library_dir) -> Path
record.bind_folder(drive_folder_id)
record.decide(local_path, size, content_hash) -> "skip" | "upload"
record.entry_for(local_path) -> SyncEntry | None
record.mark_uploaded(local_path, size, content_hash, drive_file_id)
```

Nothing in this module opens a socket.

## Later slices

2. Sign in, and let the user pick one Drive folder. Still no upload.
3. First upload of an organized library, with progress, cancel, and resume.
4. Incremental pass: skip unchanged files, upload new and changed ones.
5. Errors a non-technical user can read (quota, expired sign-in, offline).
6. Docs. The README currently says the app is offline and has no network
   features. Update that sentence in the same release that ships upload.

## Out of scope for this version

- Two-way sync.
- Deleting local files after a successful upload.
- Replacing the local organize step.
- OAuth, the Drive API, a folder picker, and the upload itself (later slices).
- A client secret, token, or credential file in the repository.
