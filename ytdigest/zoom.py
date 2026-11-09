"""Stage 8: zoom job. Extract every frame of a time range at full 720p into 2x2 sheets.

  python -m ytdigest.zoom <video_id> <H:MM:SS-H:MM:SS> <workdir> [--fps 25] [--archive-dir DIR]

Writes zooms/<video_id>/<range>/sheet_XXXX.jpg + index.md and prints the folder path.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path

from .common import FPS, ROOT, digest_dir, gh_output, hms, log, parse_ts
from .sheets import grid, label, save_jpeg, tile_text
from .video import Archive

MAX_FRAMES = 3000  # 2 minutes at 25 fps -> 750 sheets


def frame_data(vid: str, ta: float, tb: float) -> dict:
    p = digest_dir(vid) / "frames.jsonl.gz"
    out = {}
    if not p.exists():
        return out
    fa, fb = int(ta * FPS) - 1, int(tb * FPS) + 1
    with gzip.open(p, "rt") as fh:
        for line in fh:
            # cheap prefix parse: lines start with {"f":N,
            f = int(line[5:line.index(",")])
            if fa <= f <= fb:
                out[f] = json.loads(line)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("range")
    ap.add_argument("workdir", type=Path)
    ap.add_argument("--fps", type=float, default=FPS)
    ap.add_argument("--archive-dir", type=Path)
    a = ap.parse_args()
    a_s, _, b_s = a.range.partition("-")
    ta, tb = parse_ts(a_s), parse_ts(b_s)
    if tb <= ta:
        raise SystemExit(f"range end must be after start: {a.range}")
    fps = max(0.1, min(60.0, a.fps))
    if (tb - ta) * fps > MAX_FRAMES:
        raise SystemExit(f"{(tb - ta) * fps:.0f} frames requested; limit is {MAX_FRAMES} "
                         f"(e.g. 2 minutes at 25 fps). Use a shorter range or lower fps.")
    arc = Archive(a.video_id, a.workdir, a.archive_dir)
    tb = min(tb, arc.duration)
    name = re.sub(r"[^0-9.]+", "-", a_s.strip()) + "_" + re.sub(r"[^0-9.]+", "-", b_s.strip())
    name += "" if fps == FPS else f"_{fps:g}fps"
    out = ROOT / "zooms" / a.video_id / name
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("sheet_*.jpg"):
        old.unlink()
    data = frame_data(a.video_id, ta, tb)

    batch, sheets, rows = [], 0, []
    def flush():
        nonlocal batch, sheets
        if not batch:
            return
        sheets += 1
        fname = f"sheet_{sheets:04d}.jpg"
        imgs = [b[1] for b in batch]
        while len(imgs) < 4:
            imgs.append(imgs[-1] * 0)
        save_jpeg(grid(imgs, 2, 2), out / fname)
        rows.append(f"| [{fname}]({fname}) | " + " / ".join(b[0] for b in batch) + " |")
        batch = []

    n = 0
    for t, frame in arc.frames(ta, tb, fps):
        f = int(round(t * FPS))
        rec = data.get(f, {})
        extra = ""
        if rec.get("life") is not None:
            extra = f"  L{rec['life']:.0f} M{rec['mana']:.0f}"
        txt = tile_text(t, f) + extra
        batch.append((txt, label(frame, txt)))
        n += 1
        if len(batch) == 4:
            flush()
    flush()
    rel = out.relative_to(ROOT)
    (out / "index.md").write_text(
        f"# Zoom {a.video_id} {hms(ta)}-{hms(tb)} at {fps:g} fps\n\n"
        f"{n} frames at full {arc.width}x{arc.height}, 2x2 per sheet, reading order left-right, top-bottom. "
        f"Labels: HH:MM:SS.ff (frame within second at 25 fps), f = frame number at 25 fps, "
        f"L/M = life/mana % from frames.jsonl.gz when available.\n\n"
        "| sheet | frames |\n|---|---|\n" + "\n".join(rows) + "\n")
    log(f"{n} frames -> {sheets} sheets in {rel}")
    gh_output(path=str(rel), frames=str(n), sheets=str(sheets))


if __name__ == "__main__":
    main()
