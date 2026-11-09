# Profiles

The core digests **any** video the same way. A *profile* adds knowledge about one kind of content:
where the interesting things are on screen, and what counts as an event.

| profile | what it adds |
|---|---|
| `generic` (default) | nothing: the core only |
| `d2r` | Diablo II: Resurrected PvP: HUD calibration, life/mana %, poison, death, potion refills, area changes, teleports, fast movement; analysed at 25 fps |

Choose one with a line in the issue (or in a `/digest` comment):

```
profile: d2r
```

No line = `generic`. The tool may *suggest* a profile ("this looks like D2R, comment `/digest profile: d2r`")
but never switches by itself. The choice is stored in `config/videos/<video_id>.json`, so re-runs keep it.
`python -m ytdigest.profiles` lists what is installed.

## What a profile can do

A profile is a class in its own module under `ytdigest/profiles/` that defines `PROFILE = MyProfile()`.
Every hook is optional:

| hook | when it runs | what for |
|---|---|---|
| `fps` (attribute) | always | analysis rate; `None` = the video's native fps (max 60). D2R sets 25 |
| `suggest(frames)` | on a generic run, 12 sample frames | 0..1 "does this look like my content?" (a suggestion only) |
| `calibrate(arc, cfg, force, sets, samples)` | once per video | find regions on screen, return the config + an annotated image for the issue |
| `analyzer(cfg, w, h, fps)` | every frame | extra per-frame fields stored in `frames.jsonl.gz` (the generic fields are always there) |
| `derive_events(recs, fps, cfg)` | after all frames are analysed | extra events (`{"t", "f", "type", "desc", "source"}`) |
| `generic_event_types`, `dense_types`, `colors` | finalize | which generic events to keep, which event types get dense frames on the sheets, border colours |
| `generic_ocr` | analysis | `False` if the profile reads its own text regions instead of whole-frame OCR |

## Adding one (a new game, say Melee)

1. `cp ytdigest/profiles/_template.py ytdigest/profiles/melee.py`, rename the classes, set `name`, and
   uncomment `PROFILE = ...` at the bottom. (A module whose name starts with `_` is ignored.)
2. `cp config/profiles/_template.json config/profiles/melee.json` and put your regions in it
   (rectangles are `[x, y, w, h]` as fractions of the frame, so they work at any resolution).
   `Profile.defaults()` loads this file.
3. Fill in `analyzer` (read the numbers you care about into `rec`) and `derive_events`
   (turn the stored per-frame numbers into events). Keep `process` cheap: it runs on every frame; put OCR
   behind `rec["_ocr_crops"]` (it runs in a thread pool, once per second at most).
4. Try it on a local file, no GitHub needed:
   `python -m ytdigest.profiles check melee clip.mp4 config/profiles/melee.json 20`
   prints the per-frame keys it produced and the events it found in the first 20 seconds.
5. Use it: comment `/digest profile: melee` on the video's issue.

`d2r.py` is the worked example (calibration, analyzer, events, accuracy check). Behaviour of a profile
must not depend on anything outside its module except the hooks above.

Profile results are separate from the generic ones: profile events carry `"source": "<name>"`, generic
ones `"source": "generic"`; profile frame fields sit next to the generic fields in each record.
