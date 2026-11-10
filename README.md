# yt-digest: a general-purpose video-watching tool for AI

Give it any video (a YouTube link, or a file you upload) and it produces what an AI needs to *watch* it: a
the text that appears on screen, a list of visual events, frame-by-frame data, and contact sheets
of the footage with an index that says where to look. It works the same way on lectures, tutorials, interviews and
talking heads, screen recordings, slide decks, vlogs and games. Anything specific to one kind of content (a game's
health bars, say) lives in an optional **profile**; Diablo II: Resurrected PvP is just the first example.

**Visual data only:** the audio track is never downloaded, stored or analysed (no transcript, no speech, no sound
events). Everything runs in GitHub Actions. You only need the GitHub app or a phone browser.

## Phone-only quick start

1. **One-time setup**: in the repo open *Issues → Labels* and create a label called `digest`.
2. **Digest a YouTube video**: open a new issue, put the link in the body, add the label `digest`, submit.
   Optionally add a line `profile: <name>` (see *Profiles*); with no line the generic profile is used.
3. The bot comments when the run starts and again when it is done, with the folder, counts and runtime.
   If something fails it comments the last log lines instead.
4. **Zoom into a moment**: comment `/zoom <video_id> 1:23:10-1:23:14`. You get every frame of that range
   at the video's native frame rate (`/zoom <id> 1:23:10-1:23:14 15` for fewer), as 2x2 sheets in a Release.
5. Other commands (comment on the issue):

   | command | does |
   |---|---|
   | `/digest [url] [profile: x]` | run again (reuses the archived video); `profile: x` switches profile |
   | `/ingest <release-tag \| file-url> [as <name>] [profile: x]` | digest a file you uploaded (below) |
   | `/recalibrate <id>` | redo the profile's calibration and re-run the analysis |
   | `/set <id> key=value …` | set a profile's calibration values (the keys belong to the profile; `/hud` is an alias) |
   | `/zoom <id> H:MM:SS-H:MM:SS [fps]` | every frame of a range |
   | `/delete <id>` | remove a digest everywhere (files, sheets, zoom sheets, generated Release files) |

   Commands are only accepted from the repo owner, members and collaborators.
6. [`INDEX.md`](INDEX.md) lists every digest with raw links, so results can be read by URL.

## If the download fails: upload the file from your phone (Android)

YouTube sometimes refuses the runner (see *YouTube access*). The reliable fallback needs no YouTube access
from GitHub at all, and it is also how you digest **your own recordings** (a screen recording, a game capture).

1. **Get the video as a file.** Use **Seal** or **NewPipe** to download the video (choose a 720p MP4 / H.264
   format; smaller files upload faster). For your own recordings use the file as it is.
2. **Upload it to a Release** with the mobile browser (desktop-site mode works best on a phone): in this repo
   open *Releases → Draft a new release*, type a **tag** that you will remember (letters, digits, `-`, `_`, `.`,
   for example `my-video-1`), attach the file by tapping *Attach binaries*, then *Publish release*.
   - One file can be at most **2 GB**. For longer videos cut the video in two or more parts with any video
     splitter app and attach all of them to the same Release; they are joined in file-name order
     (`part1.mp4`, `part2.mp4`, …).
3. **Comment `/ingest my-video-1`** on any issue (add `profile: <name>` on the same line if you want a profile).
   It runs the same pipeline from there: archive (video only), analysis, sheets, index. The file's audio, if any, is ignored and never needed.
4. **A direct file link works too**: `/ingest https://example.com/video.mp4 as lecture1`. Only public
   `http(s)` links are fetched (not private addresses), up to 12 GB.

Your uploaded files are never deleted by the tool. `/delete <tag>` removes only what the tool generated.
(The Seal / NewPipe / mobile-browser steps are written from how those apps are documented to work and were not
tested on a phone; the `/ingest` mechanics below them were tested, see *Measured*.)

## What you get

`digests/<video_id>/`

| file | content |
|---|---|
| `index.md` | **Start here.** Maps every contact sheet to its time range, the events in it and the on-screen text. Sheet links are Release URLs. |
| `ocr.md` / `ocr.json` | On-screen text with timestamps: whole-frame OCR about once a second and on every scene change; each line is listed once, when it first appears. Slides, code, menus, chat, score counters. Low-confidence lines are marked `[?]`; text that sits at the same place through the whole video (menu bars, watermarks, overlays) is left out and listed in `ocr.json` under `furniture_suppressed`. |
| `keyframes.json` | Every keyframe: time, why it was chosen (`first`, `scene`, `changed`, `text`, `heartbeat`), and flags for the reader (below). |
| `events.md` / `events.json` | Timestamped events (generic + the profile's). |
| `frames.jsonl.gz` | One JSON record per analysed frame (keys below). |
| `thumbs/overview_NNN.jpg` | Tiny thumbnails (160x90, 96 per image, at most 360 per hour of video, evenly spaced keyframes): a browsable index that is small enough for git. Every keyframe is on the contact sheets. |
| `calibration.png` | Only for profiles that calibrate: the annotated frame. |
| `summary.json`, `meta.json` | Counts, runtime, fps, profile, sizes. |

**Contact sheets** are Release assets (`sheets-<video_id>`, `sheet_NNNN.jpg`, 900 per Release): 3x3 tiles of
640x360 with burned-in timestamps. A tile is a *keyframe* or a *dense* frame (four consecutive frames around an event:
coloured border, `HH:MM:SS.ff fN`). The video itself (720p H.264, in parts under 1.9 GB) is in the Release tagged with
the id. Videos are never committed to git.

**How keyframes are chosen** (`ytdigest/keyframes.py`): the picture is hashed (perceptual hash) once per second and a
keyframe is kept when a scene change happened, the picture changed, or 30 s passed with nothing kept; never closer
than 2 s. Near-identical frames are skipped, so a slide that stays up for ten minutes is one tile. Three things make
that work on real footage:

- **Slide-like videos** (little motion, readable text) also get a keyframe whenever the on-screen text changes
  substantially (at least half the lines are new, or the slide title changes and stays changed), because slides on a
  white background do not change the colour histogram. In such a video a small picture change with the *same* text
  (a pointer, an animation, the presenter moving) does not make a new keyframe.
- **A moving corner region** (a webcam or picture-in-picture) is detected once per video (`ytdigest/pip.py`: a
  group of cells touching a frame corner that moves on its own while the rest is quiet) and ignored by the
  picture-change measures, so a presenter's face does not split one slide into several keyframes. The pixels stay in
  every frame and sheet. A *static* overlay (logo, watermark) cannot make two identical frames look different, so it is
  not masked. Full-frame motion (gameplay, camera pans) has no such region and is left alone.
- **Screen furniture** in the OCR (menu bars, taskbars, watermarks, persistent overlays) is removed: a line that
  appears at about the same position in at least half of the OCR'd frames *and* in at least 4 of 5 equal time slices is
  furniture. Text that keeps changing at one position (a clock, a score, subtitles) is kept, and lines that differ in a
  digit count as different text.

**Look at the image.** Each keyframe is flagged when its OCR should not be trusted as the content: `math` (formulas
come out as bracket and operator soup), `dense` (12+ lines or 400+ characters) or `low_conf` (30 %+ of the lines below
confidence 72). `index.md` has a *look at the image* column listing the flagged tiles per sheet, and `keyframes.json`
has the same flags: open that tile instead of reading `ocr.md`.

### `frames.jsonl.gz` (generic fields)

| key | meaning |
|---|---|
| `f`, `t` | frame number at the analysis rate, seconds (`t = f / fps`) |
| `motion` | mean absolute pixel change vs the previous frame (0–255, whole frame, 320x180 gray) |
| `cut` | true when the colour histogram jumps (scene change) |
| `phash` | 64-bit DCT perceptual hash (hex): equal or close = same picture |
| `bright` | mean brightness 0–255 |
| `sig` | coarse scene signature: 2x2 grid of (hue bin, saturation, value) as 6 hex chars; groups similar scenes |

A profile adds its own keys next to these (the generic profile adds none).

### Generic events

`scene_change` (cuts; runs of rapid cuts merge) · `motion_burst` (large change that is not a cut; at most 6 per
minute, the strongest kept) · `static` (the picture hardly changes for ≥ 20 s: a slide, a paused video) ·
`black` (fade, loading) · `text_change` (new on-screen text). Thresholds are at the top of `ytdigest/events.py`.

## Profiles

A profile = optional calibration + extra per-frame fields + extra event rules, in its own module under
`ytdigest/profiles/`. Pick one with a line `profile: <name>` in the issue; no line = `generic`.

- **Detection is extensible.** Every profile has a detector (`detect(frames)`). On a generic run all registered
  detectors look at a few sample frames and any that recognise the content are *suggested* in the issue ("this looks like
  X, comment `/digest profile: x`"). A profile is never selected silently.
- **Adding one** needs a module and a config file: see [`ytdigest/profiles/README.md`](ytdigest/profiles/README.md)
  (a Melee template is included, not implemented; `python -m ytdigest.profiles check <name> clip.mp4` tries a profile on a
  local file).
- **Installed:** `generic` (the default) and `d2r`, a worked example for a game with an on-screen HUD
  ([`ytdigest/profiles/d2r.md`](ytdigest/profiles/d2r.md)).

## Frame rate and cost

Frames are analysed at the video's **native fps, capped at 60**, or at the profile's own rate if it sets one.
The archive keeps a constant frame rate (variable-rate phone recordings are normalised; averages below 20 fps
become 30). To cap lower, set the repository variable `FPS_CAP` (for example `30`).

Measured on a 54-minute 60 fps Melee set against the same footage at 30 fps (see *Measured*): the 60 fps archive is
about twice the size (922 MB per hour against ~470 MB) and takes about twice as long to encode (~16 min for 54 min);
`frames.jsonl.gz` is about 1.8x larger (2.5 MB for the set); analysis time grows only ~10 % because whole-frame OCR,
not the per-frame numbers, is the expensive part; the contact sheets do not change (they are chosen by content, not by
rate); `/zoom` at 60 fps gives 60 frames per second of video (15 sheets). Long videos are split into parallel jobs of
about 36,000 frames (20 min at 30 fps, 10 min at 60 fps).

## How it runs

```
issue labeled "digest"  |  /ingest <tag | url>
  └─ request     parse command, profile; comment "started"
  └─ prepare     YouTube download through WARP (or ingest) → 720p H.264 archive in Release <id>
                 → profile + calibration → commit config/videos/<id>.json → comment calibration/suggestion
       └─ analyze[matrix]  parallel chunks: every frame, whole-frame OCR, keyframe candidates
  └─ finalize    merge → events (generic + profile) → keyframes → sheets → Release sheets-<id>
                 → index.md, ocr.md, thumbs → commit digests/<id>/ → INDEX.md → comment
  └─ report-failure  comments the failing step's log lines (+ the phone-upload hint for download failures)
```

Dependencies are pinned in `requirements*.txt`; pip downloads are cached by `actions/setup-python`.

## YouTube access (no login, no cookies)

GitHub's runners sit on datacenter IPs that YouTube often answers with "Sign in to confirm you're not a bot".
This repo never uses an account. The download step (and only that step) runs through a free **Cloudflare WARP**
tunnel set up with [wgcf](https://github.com/ViRb3/wgcf) (an anonymous WARP account, created per attempt), so
YouTube sees a Cloudflare IP. If YouTube still refuses an attempt, the job registers a fresh tunnel (usually a
different IP) and tries again, up to 5 times (`scripts/download_via_warp.sh`, `scripts/warp.sh`).

What was tested without any login:

| option | result |
|---|---|
| yt-dlp from the AI sandbox | metadata works, media URLs answer 403 |
| runner, yt-dlp default / `tv` / `mweb` / `ios` / `web_safari` clients | all refused at the bot check |
| runner + bgutil PO-token provider | refused at the bot check before any token is requested (a token does not fix an IP-level flag) |
| runner + Cloudflare WARP | works on most fresh tunnels; some tunnels or IPs get refused, hence the retry loop |

WARP IPs are shared with other users, so success varies by attempt. **When it fails, use `/ingest`** (above): it
does not depend on YouTube at all. Set the repository variable `USE_WARP=false` to download without the tunnel.

## Storage

In git (`digests/<id>/`): `index.md`, `events.*`, `ocr.*`, `frames.jsonl.gz`,
`thumbs/overview_*.jpg`, `summary.json`, `meta.json`, plus the small `zooms/<id>/<range>/index.md` files.
In Releases (not git, so git history stays small): the archived video parts, `parts.json`, the contact sheets
(`sheets-<id>`), and every zoom's sheets (`zoom-<id>-<range>`). Re-running a digest deletes and recreates its
sheets Release; git history still keeps old versions of the small files. Per-hour git cost: see *Measured*.
`/delete <id>` removes a digest everywhere (not from git history).

## What this can and cannot see

Read this before trusting the output.

- **Frames are sampled, not watched.** The tool looks at one frame every 1/fps seconds (native fps, max 60; 25
  for a profile that sets one) after scaling to 320x180 for the numbers and 640x360 for the sheet tiles. Anything shorter than a frame
  interval, or smaller than a few pixels at that scale, is invisible. The contact sheets show roughly one frame
  every 2–30 s plus a few frames around events, not the whole video; use `/zoom` for a range.
- **There is no real motion or action understanding.** `motion`, `cut`, `phash`, and `sig` measure how much and
  how fast the picture changes, not what happens in it. "Large motion" can be a fight, a camera pan or a flash;
  profile rules are heuristics tuned on a few clips and can misfire (a profile's doc says how it was checked).
- **OCR makes mistakes.** Tesseract reads clean slides and UI text well, and game text, small fonts, stylised
  fonts, low contrast, motion blur and text over busy scenery poorly: expect misspellings ("Beep Moor" for
  "Blood Moor"), missing lines and some invented noise. Lines are fuzzy-deduplicated, so a real change in
  a few characters can be missed. Only English is read by default; text sizes under ~10 px at 720p are mostly lost.
  The archive is 720p, so text that needed 1080p may be unreadable.
- **Nothing that is only said is captured.** There is no transcript and no audio analysis: speech, narration, music and
  sound effects are invisible to it. A talking head shows up as cuts, keyframes and any burned-in subtitles or lower
  thirds that OCR can read; it does not know who speaks or what they say. What is shown in the pictures (recipe steps,
  stylised overlay text) comes through OCR only in fragments, so look at the images.
- **Picture-in-picture detection needs a quiet background.** A webcam is found when it moves while the rest of the picture
  is calm (slides, screen recordings); in full-motion footage there is nothing to compare against and none is reported.
- **Furniture suppression needs repetition.** Text that appears in half the OCR'd frames and across the whole video is
  dropped; a header that is on screen for only some sections, or one that changes slightly each time, stays.
- **Event counts are not ground truth.** Thresholds are fixed numbers; a different video style may need
  different ones (they are at the top of `ytdigest/events.py` and the profile).
- **The sheet selection can hide things.** Perceptual-hash dedupe skips near-identical frames; a small change
  (a number ticking over in a corner) may not make a new tile. It is still in `frames.jsonl.gz` and `ocr.md`.

## Measured

Real runs on GitHub-hosted runners (4 vCPU), no login, generic profile unless stated. The test videos were my picks.
Profile-specific results are in the profile's own doc ([`d2r.md`](ytdigest/profiles/d2r.md)).

### Generic quality fixes: before / after

| | lecture (14:29, 60 fps, slides + webcam) | 54-min 60 fps Melee set | 17-min talking-head podcast (30 fps) |
|---|---|---|---|
| OCR lines (after de-duplication) | 1,400 → **1,159** | 4,385 → **3,736** | n/a (new) |
| menu-bar / window-chrome lines | 132 (9 %) → **32 (3 %)** | 0 → 0 | 0 |
| persistent lines suppressed (listed in `ocr.json`) | 3 | 5 (the broadcast overlay: GRAND FINALS, SUPER SMASH CON 2018, PARTNERS, …) | 0 |
| keyframes | 104 → **87** | 1,530 → 1,546 (unchanged by design) | 279 → **164** |
| keyframes per slide view* | mean 3.7 → **3.0**; views with more than one: 20/25 → **15/25** | n/a | n/a |
| flagged for the reader | 31 of 87 (math 10, dense 19, low_conf 5); 245 low-confidence OCR lines marked `[?]` | 271 of 1,546 (low_conf 254, math 17, dense 4) | 8 of 164 |
| webcam / picture-in-picture | found (bottom-left, 31 % x 44 %), masked | none found | none found |
| run time from an existing archive | analysis jobs 127 s + 197 s → 171 s + 159 s, finalize 45 s → 58 s; same-machine old / new code: 248 s / 253 s (the 484 s of the first run included a 4-minute download) | 446 s → **480 s** (+8 %: extra thumbnails and flag work) | 363 s → **265 s** |

\* "slide views" = 25 segments of the lecture that stay visually stable for at least 5 s, found independently by
hashing only the slide pane once a second (the lecture has no slide numbers the OCR can read reliably). One stretch around
7:30 shows five different slides in ten seconds, which is why a few views still have many keyframes (max 14 → 16).

What each fix contributed, on the lecture (same machine and archive, only the named fix toggled): the webcam mask is
the biggest one. Without it the other fixes give 118 keyframes (worse than before: the moving webcam also triggered extra
OCR runs); with it, 85. Furniture suppression removes most of the menu-bar noise; the text-driven keyframe rule is what
keeps one keyframe per slide even though slides share a white background (the colour histogram found 0 cuts).
Two flaws that testing exposed and fixed: lines differing only by a digit (a clock, a page number) were being folded into
"furniture" (digits must now match exactly), and player tags like `[A] | ARMADA` were flagged as formulas (595 → 271 flagged
keyframes on Melee).

### What the generic output captured and missed, by kind of video

- **Lecture with slides and a webcam.** Captured: slide titles and statements ("Chebyshev's Inequality", "Union bound:
  Pr[A ∨ B] ≤ Pr[A] + Pr[B]", the theorem for Hoeffding's inequality), a keyframe per slide change, formulas flagged so the reader opens the image. Missed: formulas as text (garbled), the presenter's pointer, and
  which of five quickly flicked slides mattered.
- **Talking-head podcast with burned-in subtitles (17 min).** Captured: the subtitles read well, almost word for word
  ("The Power of Positive Thinking: Change Your Mindset, Change Your Life"), 35 scene changes (cuts between hosts and
  graphics). Subtitles changing every few seconds used to drive 136 text keyframes; the bottom caption strip is now ignored
  for keyframes (47 remain, from title cards and lower thirds). Missed: who is speaking, gestures and expressions, and
  anything said without being shown. The hosts moving still produce picture-driven keyframes (75 `changed`).
- **Cooking tutorial (10:46).** Captured: 19 scene cuts, the channel branding and an "Let's go!"-style overlay, the intro
  and outro captions. Missed: the recipe. Steps, ingredients and quantities are stylised on-screen text that OCR reads in
  fragments, and the actions (rinsing clams, stirring) are only visible in the images: no data field says "washing clams".
- **Fighting game, 54 min at 60 fps.** Captured: broadcast overlay, the match clock and the damage counters ("71% 134%"), 605
  events, dense frames at 60 fps around motion bursts. Missed: which counter belongs to which player, stocks, who won
  (what a Melee profile would add).
- **Synthetic phone screen recording** (portrait, variable frame rate, no audio): 19 of 19 known lines read exactly.

Numbers in this section were measured before the audio was removed; where a count included `silence` / `loud_peak` events
those event types no longer exist. See *Cost and storage* for the effect of dropping audio.

### Cost and storage

60 fps against 30 fps, same footage: the archive is about twice the size (830 MB for the 54-minute set, 922 MB per hour,
against ~470 MB) and takes about twice as long to encode (~16 min); `frames.jsonl.gz` is 1.8x larger (2.5 MB for the set);
analysis time is only ~10 % longer (OCR dominates); the contact sheets do not change. The Melee set took 26 min 38 s fresh
(about 41 runner-minutes), 7 min 26 s from an existing archive; `/zoom` at 60 fps gives 60 frames per second of video.

Per hour of video in git (videos and sheets are in Releases): Melee at 60 fps 4.6 MB (frames 2.5 MB, thumbnails 1.0 MB,
text 0.6 MB); the lecture 3.6 MB per hour; the podcast and cooking video about 3.1 and 5.6 MB per hour. Releases hold
the archive (922 MB per hour at 60 fps, 470 MB at 30) and the sheets (80 MB for the Melee set). Before sheets moved to
Releases a digest cost ~140 MB per hour in git; history still holds the old versions (the repo's pack is ~260 MB).

### `/ingest`, access and tests

- `/ingest` on a Release with two uploaded files (synthetic phone recording, portrait 1080x2400, variable frame rate, no
  audio): joined, normalised to 720x1600 at 30 fps, digest in 2 min 30 s. A direct
  file URL (a public 4:55 clip): 4 min 15 s. Private, loopback and cloud-metadata URLs are refused;
  `/delete` on an ingested tag removes everything generated and keeps the uploaded files. Not tested: the phone apps
  (Seal, NewPipe, the mobile release form) and files near 2 GB.
- YouTube access: WARP succeeded on attempt 1 for every download in these tests except one video that was refused 5 times in a
  minute (the same video succeeded on attempt 1 a few minutes later), so the back-off between attempts grows (5, 20, 45, 80 s);
  `/ingest` is the dependable fallback.
- Tests: `python tests/test_core.py` (14 offline checks), `tests/regression_d2r.py` (profile regression).

## Developing and testing locally

- `python -m ytdigest.profiles` lists profiles; `python -m ytdigest.profiles check <name> clip.mp4` tries one.
- `tests/regression_d2r.py` compares the d2r profile with a baseline (per-frame fields and events); `python tests/test_core.py` runs the offline checks.
- `tests/synthetic.py` and `tests/make_screen_recording.py` generate clips with known content.
- Each module's docstring says how to run it on a local archive (`--archive-dir`), no GitHub needed.

## Credits

Ideas adopted from an MIT-licensed project (thank you; an earlier version also took the caption coverage check from [jordanrendric/claude-video-vision](https://github.com/jordanrendric/claude-video-vision), since removed with the transcript):

- [usedeepmark/watch-for-me](https://github.com/usedeepmark/watch-for-me): yt-dlp failure
  classification and retrying only transient 403/blocked errors, and choosing frames by grouping near-identical
  ones by perceptual hash instead of sampling at fixed intervals.

Tools used at run time: [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense),
[wgcf](https://github.com/ViRb3/wgcf) (MIT), ffmpeg, Tesseract, OpenCV. The bgutil PO-token provider
([Brainicism/bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider),
GPL-3.0) was evaluated only in `.github/workflows/access-test.yml`, installed at run time and not
redistributed here; it did not help.
