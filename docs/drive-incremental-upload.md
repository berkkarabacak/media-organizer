# Google Drive incremental upload

Design for [issue 2](https://github.com/berkkarabacak/media-organizer/issues/2).
Later slices should follow this document.

Slice 1 is `media_organizer.core.drive_sync`. It loads and saves a local
sync record and decides whether a file is `skip` or `upload`. It does not
sign in, call the Drive API, or upload.

Slice 2 is sign-in and the folder picker:
`media_organizer.core.google_auth` (OAuth and the token file),
`media_organizer.core.drive_folders` (folder list and binding), and
`media_organizer.gui.drive_dialog` (Sign in, Sign out, Choose Drive folder).
Slice 2 does not upload file bytes and does not create Drive files.

Slice 3 is the first upload. `media_organizer.core.drive_upload` sends the
organized tree into the bound folder, one file at a time, through Drive's
resumable upload. `gui/drive_dialog` enables Upload when the user is signed
in and a folder is bound. Cancel stops before the next file. The in-flight
session can continue on the next run. This slice does not delete local or
remote files.

Slice 4 is the incremental pass. A later upload of the same library skips
every file whose size and SHA-256 still match a sync entry that has a Drive
file id. A new path is created under the bound folder. A changed file is
updated in place when that entry already has a Drive file id. Progress
counts skipped files separately from uploaded files. Byte totals count only
files that transfer: each unchanged file is removed from the total when it
is recognized, and `UploadResult.bytes_sent` is the size Drive accepted.
Cancel and resume are unchanged. This slice still does not delete local or
remote files.

Slice 5 is the message a person sees when the upload cannot continue.
`drive_upload` turns a Drive or network failure into one short sentence.
The dialog status line and the warning show that sentence. They do not
show an HTTP status, a response body, or a stack trace. A hard failure
stops the pass. Files already saved with a Drive file id stay skippable.
A resumable session that was already opened stays, so that file can
continue on the next run. Later files in the same pass are not started.

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
or a credential file. Those filenames stay in `.gitignore`.

The client id is public. `resolve_client_id` reads
`MEDIA_ORGANIZER_GOOGLE_CLIENT_ID` first. If that is unset, it uses
`BUNDLED_CLIENT_ID` in `google_auth.py`. The value shipped in the repository
is the placeholder `REPLACE_WITH_YOUR_DESKTOP_CLIENT_ID`, which the app
treats as not configured. Berk replaces that placeholder, or sets the
environment variable, with the Desktop client id from his Google Cloud
project (APIs & Services → Credentials → OAuth client ID → Desktop). Enable
the Google Picker API on that project so Choose Drive folder can open
Google's folder picker. There is no client secret to paste.

Sign-in is implemented by `GoogleSession.sign_in`. The redirect is
`http://127.0.0.1:<port>/`. Choosing a folder can open Google's desktop
Picker on that same redirect (`trigger_onepick`, `allow_folder_selection`,
folder mime type) via `GoogleSession.pick_drive_folder`. The in-app list is
`list_drive_folders` (`files.list` for folders only). With `drive.file`,
that list is only folders the app can already access. Neither call uploads.

## One Drive folder

The user picks one Drive folder. Its id is stored as `drive_folder_id` on
the sync record. Files are uploaded under that folder, keeping the
library-relative path (`2024/Q3/07 July/IMG_1234.jpg`).

`SyncRecord.bind_folder` records the id. Binding a different id drops the
stored file ids so the next pass uploads into the new folder. The files
already in the previous folder stay there. Switching back has no memory of
the old ids, so those files would upload again, still without deleting
anything. The folder picker asks before replacing a folder that already has
a record. `commit_folder_choice` returns `declined` and leaves the record
alone when the answer is no. `replaced` is `bind_folder` on a different id.

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
  `%LOCALAPPDATA%\MediaOrganizer\google\` (or
  `~/.local/share/MediaOrganizer/google/`), keyed by the library path and the
  local path. The file is `upload_sessions.json`, beside `token.json`.
  Do not store it in `drive_sync.json`.
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

Nothing in `drive_sync` opens a socket. Sign-in and folder listing live in
the slice 2 modules named above.

```text
resolve_client_id() -> str
GoogleSession.sign_in() / sign_out() / signed_in_account()
GoogleSession.access_token()
GoogleSession.list_folders() / pick_drive_folder()
commit_folder_choice(record, folder_id, replace_confirmed=...)
commit_listed_folder(record, folders, folder_id, replace_confirmed=...)
upload_library(library_dir, *, access_token, progress=None, cancel=None) -> UploadResult
```

`UploadProgress` reports the current library-relative path, files done and
total, how many files this pass has skipped and uploaded, and bytes done
and total. Those byte counts are the files that transfer. An unchanged file
is not added to `bytes_done`; its size is removed from `bytes_total` when
the pass recognizes it. `UploadResult.bytes_sent` is the same finished
transfer size. `access_token` may be a string or a callable that returns
one. When `decide` says upload and the entry already has a Drive file id,
that file is updated. A path with no file id is created once. Intermediate
folders are created under the bound folder so the library-relative path is
kept. `mark_uploaded` runs only after Drive returns a file id, then
`save_sync_record` runs, then the session URI for that path is removed.
`DriveUploadError` stops the pass. See "Errors the upload shows".

## Later slices

1. Local sync record (`skip` / `upload`, no network). Done: `drive_sync`.
2. Sign in, and let the user pick one Drive folder. Still no upload.
   Done: `google_auth`, `drive_folders`, and `gui/drive_dialog`.
3. First upload of an organized library, with progress, cancel, and resume.
   Done: `drive_upload` and the Upload button in `gui/drive_dialog`.
   The session URI is `upload_sessions.json` next to the OAuth token.
   `decide` chooses skip or upload, including when the same library is
   uploaded again.
4. Incremental pass: a later upload skips unchanged files and sends only
   new or changed ones. Done: `upload_library` and the progress line in
   `gui/drive_dialog`. A changed file with a Drive file id is updated.
   A new path is created. Skipped and uploaded counts are reported while
   the pass runs, and the byte totals leave out unchanged files.
5. Errors a non-technical user can read (quota, expired sign-in, offline).
   Done: `drive_upload` and the status line in `gui/drive_dialog`. The
   sentences are in "Errors the upload shows".
6. Docs. The README currently says the app is offline and has no network
   features. Update that sentence in the same release that ships upload.
   Not started.

## Errors the upload shows

A failure stops the upload. The pass does not skip the failed file and
continue with the next one. Nothing is deleted on Drive or on disk.
Organize stays local.

The Drive dialog shows one of these sentences. The HTTP status and the
API body stay out of the message.

| What happened | What the user sees |
|---|---|
| Drive storage is full, or Drive reports `storageQuotaExceeded`, `quotaExceeded`, or HTTP 507 | Google Drive is full. Free some space, then try the upload again. |
| The sign-in expired or was revoked: HTTP 401, `authError`, `invalid_token`, `invalid_grant`, or `UNAUTHENTICATED` | Sign in to Google Drive again. The previous sign-in expired or was revoked. |
| This computer is offline, the connection fails, or Google cannot be reached (including HTTP 502, 503, and 504) | This computer cannot reach Google Drive. Check the internet connection, then try again. |
| Any other Drive failure, including a rate limit | Google Drive did not accept the upload. Try again. |

A rate limit is not described as a full Drive. A resumable session that
Drive has closed (HTTP 404 or 410 on that session only) is not an expired
sign-in: the upload starts a new session for that one file, as in slice 3.
Files already marked uploaded are still skipped. The in-flight session URI
stays in `upload_sessions.json` when the failure happens after the session
was opened.

## Out of scope for this version

- Two-way sync.
- Deleting local files after a successful upload.
- Replacing the local organize step.
- The README offline sentence (slice 6), in the release that ships upload.
- A client secret, token, or credential file in the repository.
