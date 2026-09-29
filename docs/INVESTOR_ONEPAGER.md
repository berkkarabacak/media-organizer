# Media Organizer — Investor One-Pager

**Windows desktop app that sorts photo/video libraries by their true capture
date — not the date Windows copied them.** Live at
[berkkarabacak.github.io/media-organizer](https://berkkarabacak.github.io/media-organizer/) ·
v1.5.3 released · MIT open source.

## Problem

Phone and camera dumps accumulate for years with useless filesystem dates.
Existing tools are cloud services (privacy concern, subscriptions) or
technical utilities (no preview, no undo, overwrite risks). Nobody owns
"safe, offline, one-click photo library cleanup" on Windows.

## Product

- **True capture dates** — EXIF → video container metadata → filename
  patterns → file-date fallback, each labeled with confidence.
- **7 folder strategies**, including offline GPS-location sorting
  (bundled 584-city database, haversine nearest-city).
- **Safety as the brand:** copy-by-default, never overwrites, full dry-run
  preview table, SHA-256 duplicate detection, one-click undo,
  crash-resume journaling, free-space preflight.
- **100% offline** — no accounts, no telemetry. A selling point, not a gap.

## Engineering proof

- 188 automated pytest tests (logic + QTest GUI click-throughs)
- 131-scenario QA battery: hostile filenames (Unicode/emoji/reserved names),
  corrupt and locked files, cancel-mid-run, double-organize, 2,000-file stress
- Real-desktop UIAutomation harness drives the installed app
- On-disk crash log for field diagnostics (`%LOCALAPPDATA%\MediaOrganizer\crash.log`)

## Business model

**$19 one-time lifetime license** (launch pricing) sold via Gumroad —
zero upfront cost, ~10% per-sale fee, revenue from day one. Free tier keeps
the full app during launch to build reputation and reviews.

**Cost structure ≈ zero:** no servers, no cloud, no per-user cost. Free code
signing via the SignPath Foundation OSS program (application-ready) removes
the SmartScreen warning without a $200–400/yr certificate.

## Roadmap (post-launch)

1. SignPath signed installer + Microsoft WDSI reputation (in progress, €0)
2. Gumroad store live, license-key gating for Pro features
3. Watch-folder mode, context-menu integration ("Send to → Media Organizer")
4. Localization (Turkish first — large underserved market)
5. Microsoft Store distribution (free signing + discovery channel)

## Honest status

Brand-new launch: v1.5.3 shipped 2026-09-28, downloads just starting.
What exists today is a *finished, tested product* with distribution
(website + GitHub Releases) live — not a prototype.

## The ask

**[Founder to fill in]** — e.g. small pre-seed for 12 months runway to reach
X paying users, or strategic introduction to distribution partners.
