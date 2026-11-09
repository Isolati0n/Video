# yt-digest

Turns long Diablo II: Resurrected PvP videos (1–3 h) into files an AI can analyse:
a timestamped transcript, per-frame HUD data at 25 fps, a list of detected events, and
contact sheets of the footage. Everything runs in GitHub Actions. You only need the
GitHub app or a phone browser.

## Phone-only quick start

1. **One-time setup**
   - In the GitHub app, open the repo → **Issues** → **Labels** and create a label called `digest`.
     (In the mobile browser it is under *Issues → Labels → New label*.)
   - Optional: add the `YOUTUBE_COOKIES` secret (see below). Only needed if YouTube blocks the runner.
2. **Digest a video**: open a new issue. Put the YouTube link in the body, add the label
   `digest`, and submit.
3. The bot comments when the run starts. Then it posts a **HUD calibration image** showing
   where it found the life orb, mana orb, skill bar and text areas. Check that it looks right.
   The analysis continues with those regions anyway. If they are wrong, reply:
   - `/hud <video_id> life=x,y,w,h mana=x,y,w,h` uses percentages of the frame,
     e.g. `/hud LH9ikWH-WOU life=5,79,11,20 mana=84,79,11,20`.
     Other regions you can set: `skills=`, `chat=`, `center=`, `top_right=`. Add `full_life=0.9`
     if the orb never reads 100 %.
   - `/recalibrate <video_id>` re-runs automatic detection.

   Both commands re-run the analysis from the archived video. Nothing is downloaded again.
4. When the run finishes, the bot comments with the folder path, counts and runtime.
   If something fails, it comments the last log lines instead.
5. **Zoom into a moment**: comment `/zoom <video_id> 1:23:10-1:23:14` (optionally add an fps,
   default 25). Full-resolution frames are committed to `zooms/<video_id>/<range>/` as 2x2 sheets,
   and the bot replies with the path.
6. To re-run a digest, comment `/digest` on the issue.

Commands are only accepted from the repo owner, members and collaborators.
You can also start runs from **Actions → digest / zoom → Run workflow** (fields: URL, issue number).

## What you get

`digests/<video_id>/`

| file | content |
|---|---|
| `index.md` | Start here. Maps every contact sheet to its time range, the events in it and a transcript excerpt. |
| `transcript.md` | `[HH:MM:SS] text` lines. Uses YouTube captions when available, otherwise faster-whisper (small, int8). |
| `events.md` / `events.json` | Detected events with timestamp, frame number and a short description. |
| `frames.jsonl.gz` | One JSON object for **every frame at 25 fps** (D2R's tick rate). |
| `sheets/sheet_NNNN.jpg` | 3x3 contact sheets with 640x360 tiles, each under 1.5 MB. Tiles are one frame per second, plus 4 frames at 25 fps around each event (yellow label, coloured border). |
| `calibration.png` | The annotated calibration frame. |
| `summary.json` | Counts, stats and runtime. |

The archived video (720p H.264 CRF 26, in parts under 1.9 GB, with `parts.json` listing the
start time of each part) is attached to the **Release** tagged with the video id.
Videos are never committed to the repo.

### `frames.jsonl.gz` keys

| key | meaning |
|---|---|
| `f`, `t` | frame number at 25 fps, seconds (`t = f / 25`) |
| `scene` | `game` (HUD visible), `dark` (black/fade/loading), `other` (menu, loading art, overlay, non-game footage) |
| `hud` | 0–1 match of the HUD against the calibration reference |
| `life`, `mana` | % read from the orb fill level (null when the HUD is not visible) |
| `poison` | true when the life orb is green |
| `motion` | mean absolute pixel change vs the previous frame (0–255, game area only) |
| `cut` | true on a colour-histogram jump (scene change) |
| `phash` | 64-bit DCT perceptual hash (hex), handy for finding repeated or near-identical frames |
| `ocr` | `{region: text}` once per second for the chat, center and top-right areas, only when text was found |

### Event types

`life_drop` (≥15 % within 0.5 s) · `life_refill` / `mana_refill` (sudden refill ≥18 % within
0.4 s, likely a rejuvenation or full potion) · `poisoned` · `death` (empty life orb with the HUD
visible, or death text) · `area_change` (HUD gone 0.3–30 s around a cut or black screen, or a
new area name read by OCR) · `off_hud` (more than 30 s without the HUD) · `motion_burst` (teleport
chains, whirlwind or charge runs) · `text` (new chat or message text).
Thresholds are at the top of `ytdigest/events.py`.

## How it runs

```
issue labeled "digest"
  └─ request      parse the URL, comment "started"
  └─ prepare      yt-dlp (720p + metadata + captions) → re-encode → Release <video_id>
                  → HUD calibration → commit config/hud/<id>.json + calibration.png → comment image
       ├─ analyze[matrix]  one job per 20 min of video, every frame at 25 fps, OCR at 1/s
       └─ transcript       captions, or faster-whisper (model cached)
  └─ finalize     merge → events → contact sheets → index.md → commit digests/<id>/ → comment
  └─ report-failure  comments the failing job's log tail
```

A 3-hour video becomes 9 parallel analysis jobs, so each one stays far below the 6-hour job
limit. If the Release already exists, the download and encode are skipped. Dependencies are
pinned in `requirements*.txt`. pip downloads are cached by `actions/setup-python`, and the
whisper model by `actions/cache`.

HUD regions are stored per video as fractions of the frame in `config/hud/<video_id>.json`.
You can also edit that file in the GitHub web editor and comment `/digest` to re-run.

## If YouTube blocks the runner

GitHub runner IPs are sometimes asked to "confirm you're not a bot". When that happens the
bot says so in the issue. To fix it, add the cookies of a logged-in YouTube session:

1. On a computer (or a phone browser with a cookies.txt exporter extension), export
   `youtube.com` cookies in **Netscape cookies.txt** format.
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**,
   name `YOUTUBE_COOKIES`, and paste the whole file content.
3. Comment `/digest` on the issue.

Cookies expire. If blocks come back, export them again. Using a secondary Google account is
a good idea.

## Repo size

Contact sheets are about 0.2–0.5 MB each, roughly one sheet per 8 seconds of video plus the
event frames. A 1-hour digest adds about 100–250 MB to the repo. Delete old `digests/<id>/`
folders when you no longer need them. The Release keeps the video.

## Local / offline test (optional, for developers)

`python tests/synthetic.py /tmp/syn` writes a synthetic D2R-like clip with known life and mana
values. Run `calibrate`, `analyze`, `finalize` and `zoom` with `--archive-dir /tmp/syn` against
it. See the docstrings in `ytdigest/*.py`.
