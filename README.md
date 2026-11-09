# yt-digest

Turns long Diablo II: Resurrected PvP videos (1–3 h) into files an AI can analyse:
a timestamped transcript, per-frame HUD data at 25 fps, a list of detected events, and
contact sheets of the footage. Everything runs in GitHub Actions. You only need the
GitHub app or a phone browser.

## Phone-only quick start

1. **One-time setup**
   - In the GitHub app, open the repo → **Issues** → **Labels** and create a label called `digest`.
     (In the mobile browser it is under *Issues → Labels → New label*.)
   - Nothing else. No Google or YouTube login is ever needed (see *YouTube access* below).
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
   default 25). Full-resolution frames are made as 2x2 sheets and attached to a Release called
   `zoom-<video_id>-<range>` (they are bulky, so they stay out of git). The bot replies with the
   Release link; `zooms/<video_id>/<range>/index.md` in git holds the direct sheet links.
6. To re-run a digest, comment `/digest` on the issue.
7. To remove a video completely (digest files, zoom sheets, Release archive), comment
   `/delete <video_id>`.
8. [`INDEX.md`](INDEX.md) lists every digest with raw links, so results can be read by URL.

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
| `shift` | global camera translation vs the previous frame in px at 320 wide (phase correlation): ~0 standing, 1–3 running, ≥10 in one frame = camera jump |
| `cut` | true on a colour-histogram jump (scene change) |
| `phash` | 64-bit DCT perceptual hash (hex), handy for finding repeated or near-identical frames |
| `ocr` | `{region: text}` once per second for the chat, center and top-right areas, only when text was found |

### Event types

`life_drop` (≥10 points within 1 s) · `life_refill` / `mana_refill` (sudden refill ≥12 points within
0.6 s, likely a rejuvenation or full potion) · `poisoned` · `death` (empty life orb with the HUD
visible, or death text) · `area_change` (HUD gone 0.3–30 s around a cut or black screen, or a
new area name read by OCR) · `off_hud` (more than 30 s without the HUD) · `teleport` (one-frame camera jump
of >= 10 px at 320 wide; chains of them are merged) · `run_burst` (sustained fast camera movement:
run, whirlwind, charge) · `text` (new chat or message text). Spell flashes change `motion` but not
the camera `shift`, so they are not reported as teleports.
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

## YouTube access (no login, no cookies)

GitHub's runners sit on datacenter IPs that YouTube often answers with "Sign in to confirm you're
not a bot". This repo never uses an account. The download step (and only that step) runs through a
free **Cloudflare WARP** tunnel set up with [wgcf](https://github.com/ViRb3/wgcf) (anonymous WARP
account, created per attempt), so YouTube sees a Cloudflare IP instead. If YouTube still refuses
an attempt, the job tears the tunnel down, registers a fresh one (usually a different IP) and
tries again, up to 5 times (`scripts/download_via_warp.sh`, `scripts/warp.sh`).

What was tested without any login (details in the issue/PR history):

| option | result |
|---|---|
| yt-dlp from the AI sandbox | metadata works, media URLs answer 403 |
| runner, yt-dlp default / `tv` / `mweb` / `ios` / `web_safari` clients | all refused at the bot check |
| runner + bgutil PO-token provider | refused at the bot check before any token is requested (a token does not fix an IP-level flag) |
| runner + Cloudflare WARP | works on most fresh tunnels; some tunnels or IPs get refused, hence the retry loop |

Limits to know about: WARP IPs are shared with other users, so success varies by attempt; a
fully blocked day means re-running with `/digest` later. Set the repository variable
`USE_WARP=false` to download without the tunnel.

## Storage

In git: `index.md`, `events.md/json`, `transcript.md/json`, `frames.jsonl.gz`, `calibration.png`,
the overview sheets, `summary.json` and the small `zooms/<id>/<range>/index.md` files.
In Releases (not git): the archived video parts, `parts.json`, and every zoom's sheets.
Near-identical consecutive 1 fps tiles (menus, standing still) are skipped using the perceptual
hash (`SHEET_DEDUP_BITS`, default 3, 0 = keep all) and at least one tile every 10 s is kept.
`/delete <video_id>` removes a digest everywhere.

## Local / offline test (optional, for developers)

`python tests/synthetic.py /tmp/syn` writes a synthetic D2R-like clip with known life and mana
values. Run `calibrate`, `analyze`, `finalize` and `zoom` with `--archive-dir /tmp/syn` against
it. See the docstrings in `ytdigest/*.py`.

## Credits

Ideas adopted from two MIT-licensed projects (thank you):

- [usedeepmark/watch-for-me](https://github.com/usedeepmark/watch-for-me): yt-dlp failure
  classification and retrying only transient 403/blocked errors, and grouping near-identical frames
  by perceptual hash instead of keeping every sample.
- [jordanrendric/claude-video-vision](https://github.com/jordanrendric/claude-video-vision): the
  caption coverage check (captions that stop early or are sparse fall back to local Whisper) and the
  caption-track selection order.

Tools used at run time: [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense),
[wgcf](https://github.com/ViRb3/wgcf) (MIT), [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(MIT), ffmpeg, Tesseract, OpenCV. The bgutil PO-token provider
([Brainicism/bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider),
GPL-3.0) was evaluated only in `.github/workflows/access-test.yml`, installed at run time and not
redistributed here; it did not help.
