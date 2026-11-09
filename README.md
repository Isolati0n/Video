# yt-digest: a general-purpose video-watching tool for AI

Give it any video (a YouTube link, or a file you upload) and it produces what an AI needs to *watch* it: a
timestamped transcript, the text that appears on screen, a list of events, frame-by-frame data, and contact sheets
of the footage with an index that says where to look. It works the same way on lectures, tutorials, interviews and
talking heads, screen recordings, slide decks, vlogs and games. Anything specific to one kind of content (a game's
health bars, say) lives in an optional **profile**; Diablo II: Resurrected PvP is just the first example.

Everything runs in GitHub Actions. You only need the GitHub app or a phone browser.

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
   It runs the same pipeline from there: archive, transcript, analysis, sheets, index.
4. **A direct file link works too**: `/ingest https://example.com/video.mp4 as lecture1`. Only public
   `http(s)` links are fetched (not private addresses), up to 12 GB.

Your uploaded files are never deleted by the tool. `/delete <tag>` removes only what the tool generated.
(The Seal / NewPipe / mobile-browser steps are written from how those apps are documented to work and were not
tested on a phone; the `/ingest` mechanics below them were tested, see *Measured*.)

## What you get

`digests/<video_id>/`

| file | content |
|---|---|
| `index.md` | **Start here.** Maps every contact sheet to its time range, the events in it, the on-screen text and the transcript excerpt. Sheet links are Release URLs. |
| `transcript.md` | `[HH:MM:SS] text`. YouTube captions when they cover the video, otherwise faster-whisper (small, int8; language detected from the loudest windows). |
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
`black` (fade, loading) · `text_change` (new on-screen text) · `silence` (≥ 8 s below −50 dBFS) ·
`loud_peak` (≥ 12 dB over the typical level). Thresholds are at the top of `ytdigest/events.py` and `audio.py`.

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
       ├─ analyze[matrix]  parallel chunks: every frame, whole-frame OCR, keyframe candidates
       └─ transcript       captions (if they cover the video) or faster-whisper
  └─ finalize    merge → events (generic + profile + audio) → keyframes → sheets → Release sheets-<id>
                 → index.md, ocr.md, thumbs → commit digests/<id>/ → INDEX.md → comment
  └─ report-failure  comments the failing step's log lines (+ the phone-upload hint for download failures)
```

Dependencies are pinned in `requirements*.txt`; pip downloads are cached by `actions/setup-python`, the Whisper
model by `actions/cache`.

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

In git (`digests/<id>/`): `index.md`, `events.*`, `transcript.*`, `ocr.*`, `frames.jsonl.gz`,
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
- **Audio events are only loudness.** `silence` and `loud_peak` cannot tell speech from music or a cheer from
  an explosion. Whisper (small, int8) is good on clear speech, weaker on crosstalk, accents, music and game noise;
  captions from YouTube (often auto-generated) are used when they span ≥ 60 % of the video.
- **Event counts are not ground truth.** Thresholds are fixed numbers; a different video style may need
  different ones (they are at the top of `ytdigest/events.py`, `audio.py`, and the profile).
- **The sheet selection can hide things.** Perceptual-hash dedupe skips near-identical frames; a small change
  (a number ticking over in a corner) may not make a new tile. It is still in `frames.jsonl.gz` and `ocr.md`.

## Measured

Real runs on GitHub-hosted runners (4 vCPU), no login. "Fresh" = download or ingest, archive and analysis.
Numbers are from this repo's Actions runs; the test videos were my picks (the owner did not supply any).

**(a) D2R, 4:55, `d2r` profile: regression against the pre-profile code.** Same results as before the restructure:
the profile's 78 events (text 10, teleport 53, life_drop 5, mana_refill 8, death 2) and the orb accuracy (life median
0.43 % / mean 4.4 %, mana median 1.03 % / mean 6.52 % against the game's on-screen numbers) are identical.
Checked two ways: on the same machine and archive the old and new code agree on **all 7,365 frames, every per-frame
field** (0 mismatches) and the event list; the calibration (orb geometry, fitted curves) is identical
(`tests/regression_d2r.py`). Run time 3 min 56 s. The profile now also adds 6 generic audio events (silence, loud peaks);
D2R keeps its own text regions instead of whole-frame OCR.

**(b) The same video, `generic` profile (30 fps).** 3 min 24 s, 8,838 frames, 76 events: text_change 40, motion_burst 29
(both at their per-minute caps), silence 4, loud_peak 2, scene_change 1. 146 keyframes of 288 candidates, 31 sheets (5.9 MB,
in a Release), 0.44 MB in git. The generic OCR reads the clean parts ("Level 94", "Ping:", "Game:", names, the area name
"Beep Moor" for "Blood Moor", "Fine ENCHANTED") with many misreads and near-duplicates; it also suggested the d2r profile
(90 %) without switching to it. What it cannot do: life/mana %, deaths, teleports (those are the profile's).

**(c) Non-game: a 14:29 machine-learning lecture (slides + webcam, itself a 60 fps screen recording).** 8 min of run
time (download 4 min incl. WARP, analysis 2 parallel jobs of 2–3 min, captions used, finalize 45 s). 52,143 frames, 136 events:
text_change 120 (cap), motion_burst 15 (mostly slide changes and a window switch, with dense frames at 60 fps), loud_peak 1;
`scene_change` found 0 cuts (slides share a white background, so the colour histogram does not jump; the slide changes show
up as `text_change`, `motion_burst` and new keyframes). 104 keyframes of 291, 19 sheets. OCR: titles and statements are
captured ("Basic Properties", "Chebyshev's Inequality", "Jensen's Inequality", "Hoeffding's Inequality", "Union bound:
Pr[A ∨ B] ≤ Pr[A] + Pr[B]", "Theorem: Let X1,…,Xm be indep. rand. variables with the same expectation …"); formulas are
often garbled, and a large part of the 1,400 lines is the macOS menu bar and window chrome ("Tools Window Help", "Preview File"),
which the fuzzy de-duplication only partly folds. The same presenter slide appears in 3 keyframes because the webcam
corner changes the picture hash.
A synthetic phone screen recording (portrait, variable frame rate, 6 screens of known text) was read **19/19 lines exactly**.

**(d) 60 fps fighting game: a 54:10 Melee set (SSC 2018, generic profile).** Fresh run 26 min 38 s (prepare 19 min 30 s:
download 1 min, encode ~16 min, upload; six parallel analysis jobs 2.4–4.4 min each; captions; finalize 2 min 40 s);
7 min 26 s when the archive already exists. About 41 runner-minutes. 195,047 frames, 605 events (text_change 424,
motion_burst 145, scene_change 25, loud_peak 11); 1,530 keyframes, 251 sheets (80 MB). The OCR picks up the broadcast
overlay (event name, player tags, the match clock) and the damage counters ("71% 134%") but cannot say which belongs to which
player: that is what a Melee profile would add.

**Storage per hour of video** (digest in git; videos and sheets are in Releases):

| video | in git | archive in Release | sheets in Release |
|---|---|---|---|
| Melee 54 min, 60 fps, generic | 4.1 MB (4.6 MB/h): frames 2.5 MB, thumbnails 1.0 MB, text 0.6 MB | 830 MB | 80 MB |
| D2R 57 min, 25 fps, d2r | 3.6 MB (3.8 MB/h) | 444 MB | 48 MB |
| lecture 14.5 min, 60 fps | 0.86 MB (3.6 MB/h) | 41 MB | 4.5 MB |

Before this change the D2R digest kept its sheets in git: 136 MB for the 57-minute video. Git history still holds those old
versions (the repo's pack is ~260 MB); new digests add only the numbers above. A 3-hour 60 fps video is extrapolated, not
tested: ~14 MB in git, ~2.8 GB of archive in 2 parts, ~250 MB of sheets, one encode job of about 50 minutes.

**`/ingest`.** Tested on Actions: (1) a Release with two uploaded files made through the normal release mechanism
(a synthetic phone screen recording, portrait 1080x2400, variable frame rate, no audio, split in two): joined, normalised to
720x1600 at 30 fps, transcript step reports "no audio track", digest in 2 min 30 s; (2) a direct file URL
(the 4:55 clip, no captions so Whisper ran): 4 min 15 s; (3) private, loopback and cloud-metadata URLs are refused;
(4) `/delete` on an ingested tag removes everything generated and keeps the uploaded files; (5) the failure comment
carries the upload hint (seen when WARP was refused on all 5 attempts for the lecture; a retry minutes later succeeded on
the first attempt). Not tested: the actual phone steps (Seal, NewPipe, the mobile browser's release form), files near 2 GB.

**YouTube access since the last report.** WARP succeeded on attempt 1 for the Melee set and for the lecture's second try (the D2R
re-runs reused their archives and downloaded nothing); the lecture's first try was refused on all 5 attempts within a minute. Refusals come in streaks, so the
back-off between attempts now grows (5, 20, 45, 80 s). `/ingest` is the dependable fallback.

**Unit tests**: `python tests/test_core.py` (7 offline checks), `tests/regression_d2r.py`.


## Developing and testing locally

- `python -m ytdigest.profiles` lists profiles; `python -m ytdigest.profiles check <name> clip.mp4` tries one.
- `tests/regression_d2r.py` compares the d2r profile with a baseline (per-frame fields and events); `python tests/test_core.py` runs the offline checks.
- `tests/synthetic.py` and `tests/make_screen_recording.py` generate clips with known content.
- Each module's docstring says how to run it on a local archive (`--archive-dir`), no GitHub needed.

## Credits

Ideas adopted from two MIT-licensed projects (thank you):

- [usedeepmark/watch-for-me](https://github.com/usedeepmark/watch-for-me): yt-dlp failure
  classification and retrying only transient 403/blocked errors, and choosing frames by grouping near-identical
  ones by perceptual hash instead of sampling at fixed intervals.
- [jordanrendric/claude-video-vision](https://github.com/jordanrendric/claude-video-vision): the
  caption coverage check (captions that stop early or are sparse fall back to local Whisper) and the
  caption-track selection order.

Tools used at run time: [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense),
[wgcf](https://github.com/ViRb3/wgcf) (MIT), [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(MIT), ffmpeg, Tesseract, OpenCV. The bgutil PO-token provider
([Brainicism/bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider),
GPL-3.0) was evaluated only in `.github/workflows/access-test.yml`, installed at run time and not
redistributed here; it did not help.
