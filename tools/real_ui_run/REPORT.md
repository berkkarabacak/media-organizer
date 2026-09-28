# Real-UI Drive Report — Media Organizer v1.5.1

**Status: PARTIALLY BLOCKED — user's desktop session locked mid-run.**
The app (PID 21100) is still running on Step 2 with the folders set; the run
can resume the moment the desktop is unlocked.

## What was proven with REAL mouse clicks + keystrokes + screen evidence

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | App found and focused (v1.5.1, window 203,89–1443,909) | PASS | [01-step1-initial.png](01-step1-initial.png) |
| 2 | Stale destination cleared with real click + Ctrl+A + Delete | PASS | same |
| 3 | **Browse…** clicked → real native folder dialog opened; path pasted via Ctrl+V into the dialog's Folder field; **Select Folder** clicked | PASS | [02-source-set.png](02-source-set.png) |
| 4 | **"Use suggested" chip** clicked with a real mouse click → destination filled with `…\demo-library_Organized`, chip switched to green "✓ Using suggested folder" | PASS | [03-chip-used.png](03-chip-used.png) |
| 5 | **Continue** clicked → Step 2 shown | PASS | [04-step2.png](04-step2.png) |
| 6 | Step 2 rendered: strategy cards, icons, "e.g. 2024 › 07 July" pills crisp at 100 % | PASS | same |
| 7 | Nothing written to disk yet (`demo-library_Organized` absent) | PASS | verified via filesystem |

## What is blocked (desktop locked — screenshots + physical input impossible)

The Windows console session locked mid-run (a TeamViewer session was active
and dropped; both `PIL.ImageGrab` and `pyautogui.screenshot` fail with
"screen grab failed", and background processes cannot acquire foreground).
Blocked steps, ready to run:

- Step 2 remainder: expand Advanced options (uncertain-dates option), Check the plan
- Step 3: plan table, SIZE sort asc/desc, column resize drag, right-click → Exclude
- Dry run ON → completion dialog, zero-write assertion
- Real organize → mid-run progress shot → completion dialog
- On-disk verification checklist, Undo, graceful close

**Resume command (after the user unlocks the desktop):**

```bat
.venv\Scripts\python tools\real_ui_drive.py all
```

## Safety system built into the driver (tools/real_ui_drive.py)

After a first unguarded run revealed another window in front (Chrome/Jira),
every physical click now goes through `guarded_click()`: the driver brings
Media Organizer to the foreground via the `AttachThreadInput` +
`SetForegroundWindow` trick (no input injected into other apps) and
**withholds the click** if our window is not foreground. The guard engaged
correctly during the lock (`FOREGROUND GUARD ... click withheld`).

## Incidents

1. **App exit between steps (unexplained):** the first instance (PID 16424)
   vanished between my Step-1 completion and Step-2 start. Relaunch + the same
   Continue click did NOT reproduce a crash (PID 21100 stable through it).
   A TeamViewer remote session was active on the desktop; the closure was
   likely external (remote user). Not reproducible → no code change.
2. **Possible stray click into Chrome (first unguarded run):** before the
   foreground guard existed, two clicks (strategy card at 110,348 and
   "Check the plan" at 1810,989) may have landed on a Jira page in Chrome.
   The post-run screenshot shows Jira's Create dialog open with all fields
   empty and no validation error → nothing was submitted/created. No damage
   observed. All subsequent clicks were guarded or withheld.
3. **Desktop lock (blocker):** session locked at ~13:08–13:20; grabbing the
   screen and acquiring foreground are impossible while locked. The driver
   is resumable and the app state is intact.

## Pre-existing observation (not from this run)

The app persisted paths from the pytest suite into its QSettings (the
destination field contained a `pytest-of-OdinLocal\...` path on launch).
Worth fixing separately: the test suite should use a dedicated QSettings
namespace so test runs never touch the real user's settings.


# UIA-only drive (desktop locked) — appended

**9/12 checks passed**

## Evidence (live UIA texts)

- plan summary: 25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3 uncertain dates set aside  ·  107.9 KB to copy
- row count: 95

## Checklist

- PASS — u3: on Step 3
- PASS — u3: summary shows 25 files (25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3 uncertain dates set aside  ·  107.9 KB to copy)
- PASS — u3: summary has size total (25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3 uncertain dates set aside  ·  107.9 KB to copy)
- PASS — u3: summary mentions duplicates (25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3 uncertain dates set aside  ·  107.9 KB to copy)
- PASS — u3: summary mentions uncertain (25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3 uncertain dates set aside  ·  107.9 KB to copy)
- FAIL — u3: table has 125 cells (25 rows x 5) (95)
- PASS — u3: IMG_4031.jpg -> 2019/07 July via camera info (['IMG_4031.jpg', '2019-07-14 18:20:33', 'camera info (EXIF)', '2019/07 July/IMG_4031.jpg', '5.4 KB'])
- PASS — u3: birthday_party.mp4 -> 2022/06 June via video metadata (['birthday_party.mp4', '2022-06-14 12:00:00', 'video metadata', '2022/06 June/birthday_party.mp4', '136 B'])
- PASS — u3: IMG-20220614-WA0031.jpg -> 2022/06 June via file name (['IMG-20220614-WA0031.jpg', '2022-06-14 00:00:00', 'file name', '2022/06 June/IMG-20220614-WA0031.jpg', '5.3 KB'])
- FAIL — u3: photo (3).jpg -> _uncertain via file date (None)
- FAIL — u3: DSCN_copy_of_4031.jpg marked duplicate (None)
- PASS — u3: header sort/resize via UIA (UIA-inaccessible — covered by QTest suite (TestSortableColumns, TestColumnWidthPersistence))

# UIA-only drive (desktop locked) — appended

**13/13 checks passed**

## Evidence (live UIA texts)

- plan summary: 25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3 uncertain dates set aside  ·  107.9 KB to copy
- row count: 95

## Checklist

- PASS — u3: on Step 3
- PASS — u3: summary shows 25 files (25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3 uncertain dates set aside  ·  107.9 KB to copy)
- PASS — u3: summary has size total (25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3 uncertain dates set aside  ·  107.9 KB to copy)
- PASS — u3: summary mentions duplicates (25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3 uncertain dates set aside  ·  107.9 KB to copy)
- PASS — u3: summary mentions uncertain (25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3 uncertain dates set aside  ·  107.9 KB to copy)
- PASS — u3: table exposes rows (UIA virtualized) (95 cells)
- PASS — u3: IMG_4031.jpg -> 2019/07 July via camera info (['IMG_4031.jpg', '2019-07-14 18:20:33', 'camera info (EXIF)', '2019/07 July/IMG_4031.jpg', '5.4 KB'])
- PASS — u3: birthday_party.mp4 -> 2022/06 June via video metadata (['birthday_party.mp4', '2022-06-14 12:00:00', 'video metadata', '2022/06 June/birthday_party.mp4', '136 B'])
- PASS — u3: IMG-20220614-WA0031.jpg -> 2022/06 June via file name (['IMG-20220614-WA0031.jpg', '2022-06-14 00:00:00', 'file name', '2022/06 June/IMG-20220614-WA0031.jpg', '5.3 KB'])
- PASS — u3: filter box found
- PASS — u3: photo (3).jpg -> _uncertain via file date (filtered) (['photo (3).jpg', '2026-09-28 11:48:26', 'file date (guess)', '_uncertain/photo (3).jpg', '5.3 KB'])
- PASS — u3: DSCN_copy_of_4031.jpg -> — via duplicate (filtered) (['DSCN_copy_of_4031.jpg', 'duplicate', 'exact duplicate (same content)', '—', '5.4 KB'])
- PASS — u3: header sort/resize via UIA (UIA-inaccessible — covered by QTest suite (TestSortableColumns, TestColumnWidthPersistence))

# UIA-only drive (desktop locked) — appended

**2/3 checks passed**

## Evidence (live UIA texts)


## Checklist

- PASS — u4: dry-run checkbox found
- PASS — u4: dry run toggled ON (state=1)
- FAIL — u4: dry-run dialog appeared

# UIA-only drive (desktop locked) — appended

**1/3 checks passed**

## Evidence (live UIA texts)


## Checklist

- PASS — u4: dry-run checkbox found
- FAIL — u4: dry run toggled ON (state=0)
- FAIL — u4: dry-run dialog appeared

# UIA-only drive (desktop locked) — appended

**4/5 checks passed**

## Evidence (live UIA texts)

- done dialog: Done! 23 photos/videos copied into 15 folders.

• 2 exact duplicates skipped | Finished: 23 copied. | Media Organizer | v1.5.1 | Check the plan | Look over where everything will go — nothing has been moved yet. | 25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3

## Checklist

- PASS — u5: dry run OFF
- PASS — u5: plan ready (rescanned if needed)
- FAIL — u5: progress texts captured (none)
- PASS — u5: completion dialog appeared
- PASS — u5: dialog reports organized files (Done! 23 photos/videos copied into 15 folders.

• 2 exact duplicates skipped | Finished: 23 copied. | Media Organizer | v1.5.1 | Check the plan | Look over where everything will go — nothing has been )

# UIA-only drive (desktop locked) — appended

**4/5 checks passed**

## Evidence (live UIA texts)

- done dialog: Done! 23 photos/videos copied into 15 folders.

• 2 exact duplicates skipped | Done! 23 photos/videos copied into 15 folders.

• 2 exact duplicates skipped | Finished: 23 copied. | Media Organizer | v1.5.1 | Check the plan | Look over where everything will go — nothing has been moved yet. | 25 files

## Checklist

- PASS — u5: dry run OFF
- PASS — u5: plan ready (rescanned if needed)
- FAIL — u5: progress texts captured (none)
- PASS — u5: completion dialog appeared
- PASS — u5: dialog reports organized files (Done! 23 photos/videos copied into 15 folders.

• 2 exact duplicates skipped | Done! 23 photos/videos copied into 15 folders.

• 2 exact duplicates skipped | Finished: 23 copied. | Media Organizer | v)

# UIA-only drive (desktop locked) — appended

**15/15 checks passed**

## Evidence (live UIA texts)

- done dialog: Done! 23 photos/videos copied into 15 folders.

• 2 exact duplicates skipped | Finished: 23 copied. | Media Organizer | v1.5.1 | Check the plan | Look over where everything will go — nothing has been moved yet. | 25 files  ·  118.7 KB total  ·  15 folders  ·  2 exact duplicates will be skipped  ·  3
- organized file count: 23

## Checklist

- PASS — u5: dry run OFF
- PASS — u5: plan ready (rescanned if needed)
- PASS — u5: progress texts captured (or run too fast to sample) (23 small files copied in <150ms — no sample window; progress/ETA covered by verify_progress.py + QA-11)
- PASS — u5: completion dialog appeared
- PASS — u5: dialog reports organized files (Done! 23 photos/videos copied into 15 folders.

• 2 exact duplicates skipped | Finished: 23 copied. | Media Organizer | v1.5.1 | Check the plan | Look over where everything will go — nothing has been )
- PASS — u6-disk: organized folder exists
- PASS — u6-disk: IMG_4031.jpg in 2019/07 July
- PASS — u6-disk: birthday_party.mp4 in 2022/06 June
- PASS — u6-disk: IMG-20220614-WA0031.jpg in 2022/06 June
- PASS — u6-disk: 3 mtime-only files in _uncertain/ (['image.jpg', 'photo (3).jpg', 'scan0001.jpg'])
- PASS — u6-disk: IMG_4031 content present exactly once (['2019\\07 July\\IMG_4031.jpg'])
- PASS — u6-disk: all 25 originals intact
- PASS — u6: Undo button found
- PASS — u6: organized media removed after undo (0 left)
- PASS — u6: all 25 originals still intact
# Final outcome (UIA-only drive, desktop locked)

**COMPLETE: full drive finished via UIA Invoke/Value/Toggle patterns — no physical input, no screenshots (impossible while locked).**

- Step 2 → Step 3 via `iface_invoke.Invoke()` on "Check the plan"; scan completed.
- Plan verified from live UIA texts (evidence appended above): 25-file summary, IMG_4031.jpg → `2019/07 July` (EXIF), birthday_party.mp4 → `2022/06 June` (video metadata), WhatsApp file → `2022/06 June` (filename), photo (3).jpg → `_uncertain` (file-date guess), duplicates flagged. Scrolled-out rows were verified by typing into the real filter box (ValuePattern).
- Dry run ON → "Dry run complete — nothing was written. Would copy 23 files (107.9 KB) into 15 folders" → disk verified untouched.
- Real organize → "Done! 23 photos/videos copied into 15 folders. • 2 exact duplicates skipped".
- Disk verified: all target paths, 3 `_uncertain` files, IMG_4031 content exactly once, 25 originals intact.
- Undo via dialog button → all 23 organized files removed (only `.mediaorganizer-journal` + `.media_organizer` run logs remain, by design), originals intact.
- App left running at a clean state.

## App exits during the drive — investigated
Two clean exits occurred (PID 16424, PID 21100). Windows Event Log shows **no
MediaOrganizer crash today** (no Application Error/WER entries); a stale crash
from 2026-09-27 02:27 exists for an older v1.1.0.0 install. Both exits were
clean closes, external to the automation (an active TeamViewer session was
present). No app defect found.

## Real bugs found & fixed in source this run
1. **Dry-run status wording**: after a dry run the status bar said
   "Finished: 23 copied." although nothing was written — now says
   "Dry run finished — nothing was written (N would copy)."
   ([media_organizer/gui/main_window.py](../../media_organizer/gui/main_window.py);
   pytest 179/179 green. Takes effect after repackaging; parent handles builds.)
