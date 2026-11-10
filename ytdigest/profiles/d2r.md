# The `d2r` profile (Diablo II: Resurrected PvP)

One example profile. Select it with `profile: d2r` in the issue, or comment `/digest profile: d2r`. The generic
detector suggests it when the first frames show the D2R orb HUD (it never switches by itself).

## What it adds

- **Calibration** (once per video, posted as an annotated image in the issue): finds the life and mana orbs, the skill
  bar and the text areas. If the game prints `Life: x / y` and `Mana: x / y` above the orbs it reads those with
  Tesseract and uses them as ground truth: half of the samples fit a curve (liquid height → value), the other half
  report the error, so the comment says how accurate the orb readings are. Override regions with
  `/set <id> life=x,y,w,h mana=x,y,w,h [skills=..] [chat=..] [center=..] [top_right=..] [full_life=0.9]`
  (percent of the frame; `/hud` is an alias) or redo it with `/recalibrate <id>`.
- **Per-frame fields** (next to the generic ones): `scene` (game / dark / other), `hud`, `life`, `mana`, `poison`,
  `motion_game`, `shift`, `match`, `chg` (camera tracking), and `ocr` (text regions: chat, centre messages, area name).
- **Events:** `life_drop`, `life_refill`, `mana_refill`, `poisoned`, `death`, `area_change`, `off_hud`, `teleport` (a frame
  where the whole view changes and the new view does not match the old one: motion high, structural match low, most blocks
  changed), `run_burst` (sustained camera movement), `text` (new chat / message text).
- **Analysis rate:** 25 fps, D2R's tick rate, whatever the video's own rate is.
- It reads its own text regions, so it turns off whole-frame OCR, and it keeps only the generic `static` event.

## Measured

- **Regression against the pre-profile code** (the profile's reason to exist unchanged): on the same machine and archive the old
  and new code agree on all 7,365 frames of a 4:55 clip (every per-frame field, 0 mismatches), on the 78 events (text 10,
  teleport 53, life_drop 5, mana_refill 8, death 2) and on the calibration (orb geometry and fitted curves). On Actions the
  run took 3 min 56 s. Re-checked after the generic quality fixes: still 0 mismatches.
- **Orb accuracy** against the game's on-screen numbers, on frames held out of the fit: life median error 0.43 % (mean 4.4 %),
  mana 1.03 % (6.52 %) on the 4:55 clip; life 1.4 % (2.8 %), mana 2.2 % (9.7 %, only 27 readable samples) on a 57-minute video.
  Means are higher than medians because a few of the reference numbers are OCR digit slips.
- **Teleports** are a heuristic. Of about 15 events checked by eye (before/after frames), most were real view changes; the misses were a
  UI panel opening and a spell effect that left the view unchanged.
- **Storage** (57 minutes at 25 fps): 3.6 MB in git (3.8 MB per hour), 444 MB archive and 48 MB of sheets in Releases.
