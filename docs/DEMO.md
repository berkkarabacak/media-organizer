# Investor Demo Script — Media Organizer (5 minutes)

**Goal:** show a real, working, safety-first product with a clear monetization
path — not slideware.

## Prep (2 min before the meeting)

```bat
.venv\Scripts\python tools\make_demo_library.py    :: regenerates demo-library\ (25 files, every detection case)
.venv\Scripts\python main.py                        :: or run the installed app
```

Also open the website in a browser tab: <https://berkkarabacak.github.io/media-organizer/>

## The run

**0:00 — The problem (website, 30 s).**
"Everyone's photos are scattered across folders with broken dates — Windows
records the *copy* date, not the moment. Scroll the hero → the problem section."

**0:30 — Step 1: folders (app).**
Pick `demo-library/` as source. Point out the suggestion chip
(`demo-library_Organized`). *"One click, sane defaults."*

**1:00 — Step 2: strategy (app).**
Show the 7 strategy cards, hover **Year → Month**, then **By location (GPS)**.
*"Seven structures including offline GPS-location sorting — 584-city database,
no network."* Open *Advanced options*: copy vs move, duplicate skipping,
uncertain-date handling.

**2:00 — Step 3: the plan (app — the money shot).**
The preview table shows every file with its detected date **and how it was
found**: EXIF, video container metadata, filename pattern, file-date guess —
each labeled by confidence. Duplicates flagged by content hash. *"Nothing has
moved yet. This is the dry-run promise."*

**3:00 — Organize (app).**
Press **Organize now** — determinate progress bar with percent, throughput and
ETA. Completes in seconds on the demo library. Show the completion dialog.

**3:30 — Undo (app — the trust closer).**
*"Every run is reversible."* Click **Undo** — files restored. Then open the
organized folder to show the clean `2024/07 July/` structure.

**4:00 — Quality & business (website).**
Scroll to Screenshots → Pricing.
- *"Engineering discipline: 188 automated tests, a 131-scenario QA battery,
  and a UIAutomation harness that drives the real installed app."*
- *"Business model: $19 one-time lifetime license via Gumroad — zero upfront
  cost, revenue from day one. Free code signing via the SignPath Foundation
  program is in progress to remove the SmartScreen warning."*

**4:30 — The ask.** (See INVESTOR_ONEPAGER.md.)

## Fallbacks

- App won't launch? Show the screenshots on the website — they're the real UI.
- Demo library stale? Re-run `make_demo_library.py` — takes 2 seconds.
- Questions on safety: README "Safety guarantees" section; crash log at
  `%LOCALAPPDATA%\MediaOrganizer\crash.log`.
