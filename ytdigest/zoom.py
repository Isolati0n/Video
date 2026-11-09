"""Stage 8: zoom job. Extract every frame of a time range at full 720p into 2x2 sheets.

  python -m ytdigest.zoom <video_id> <H:MM:SS-H:MM:SS> <workdir> [--fps 25] [--archive-dir DIR]

Writes zooms/<video_id>/<range>/sheet_XXXX.jpg + index.md and prints the folder path.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
from pathlib import Path

from . import profiles, videoconfig
from .common import GH, MAX_FPS, ROOT, digest_dir, gh_output, load_json, log, parse_ts
from .sheets import grid, label, save_jpeg, tile_text
from .video import Archive

MAX_FRAMES = 3000  # 2 minutes at 25 fps -> 750 sheets


def frame_data(vid: str, ta: float, tb: float, afps: float) -> dict:
    """Stored per-frame records of the digest (analysis-rate frame numbers) around the range."""
    p = digest_dir(vid) / "frames.jsonl.gz"
    out = {}
    if not p.exists():
        return out
    fa, fb = int(ta * afps) - 1, int(tb * afps) + 1
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
    ap.add_argument("--fps", default="", help="frames per second; default = the digest's analysis rate (native fps, or the profile's)")
    ap.add_argument("--archive-dir", type=Path)
    a = ap.parse_args()
    a_s, _, b_s = a.range.partition("-")
    ta, tb = parse_ts(a_s), parse_ts(b_s)
    if tb <= ta:
        raise SystemExit(f"range end must be after start: {a.range}")
    arc = Archive(a.video_id, a.workdir, a.archive_dir)
    meta = load_json(digest_dir(a.video_id) / "meta.json", {}) or {}
    saved = videoconfig.load(a.video_id) or {}
    afps = float(meta.get("fps") or profiles.analysis_fps(arc.native_fps, profiles.get(saved.get("profile"))))
    explicit = bool(str(a.fps).strip())
    fps = max(0.1, min(float(MAX_FPS), float(a.fps))) if explicit else min(arc.native_fps, float(MAX_FPS))
    if (tb - ta) * fps > MAX_FRAMES:
        raise SystemExit(f"{(tb - ta) * fps:.0f} frames requested; limit is {MAX_FRAMES} "
                         f"({MAX_FRAMES / fps:.0f} s at {fps:g} fps). Use a shorter range or a lower fps.")
    tb = min(tb, arc.duration)
    name = re.sub(r"[^0-9.]+", "-", a_s.strip()) + "_" + re.sub(r"[^0-9.]+", "-", b_s.strip())
    name += f"_{fps:g}fps" if explicit else ""
    tag = f"zoom-{a.video_id}-{name}"[:120]
    out = a.workdir / "zoom_out" / name  # sheets are bulky: they go to a Release, not to git
    out.mkdir(parents=True, exist_ok=True)
    slug = os.environ.get("GITHUB_REPOSITORY", "OWNER/REPO")
    asset_base = f"https://github.com/{slug}/releases/download/{tag}"
    data = frame_data(a.video_id, ta, tb, afps)

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
        rows.append(f"| [{fname}]({asset_base}/{fname}) | " + " / ".join(b[0] for b in batch) + " |")
        batch = []

    n = 0
    for t, frame in arc.frames(ta, tb, fps):
        f = int(round(t * fps))
        rec = data.get(int(round(t * afps)), {})
        extra = ""
        if rec.get("life") is not None:
            extra = f"  L{rec['life']:.0f} M{rec['mana']:.0f}"
        txt = tile_text(t, f, fps=fps) + extra
        batch.append((txt, label(frame, txt)))
        n += 1
        if len(batch) == 4:
            flush()
    flush()
    (out / "index.md").write_text(
        f"# Zoom {a.video_id} {a_s.strip()}-{b_s.strip()} at {fps:g} fps\n\n"
        f"{n} frames at full {arc.width}x{arc.height}, 2x2 per sheet, reading order left-right, top-bottom. "
        f"Labels: HH:MM:SS.ff (frame within the second at {fps:g} fps), f = frame number at {fps:g} fps, "
        f"L/M = life/mana % from frames.jsonl.gz when available. Sheets are Release assets: "
        f"{asset_base}/sheet_NNNN.jpg\n\n"
        "| sheet | frames |\n|---|---|\n" + "\n".join(rows) + "\n")

    # bulky part -> its own Release (one per zoom); only the small index.md is committed to git
    gh = GH()
    rel = gh.ensure_release(tag, f"Zoom {a.video_id} {a_s.strip()}-{b_s.strip()}",
                            f"{n} frames at {fps:g} fps from `{a.video_id}`, {sheets} sheets.")
    files = sorted(out.glob("sheet_*.jpg")) + [out / "index.md"]
    for i, f in enumerate(files, 1):
        gh.upload_asset(rel, f, "image/jpeg" if f.suffix == ".jpg" else "text/markdown")
        if i % 25 == 0:
            log(f"uploaded {i}/{len(files)}")
    rel_dir = ROOT / "zooms" / a.video_id / name
    rel_dir.mkdir(parents=True, exist_ok=True)
    (rel_dir / "index.md").write_text((out / "index.md").read_text())
    log(f"{n} frames -> {sheets} sheets in release {tag}")
    gh_output(path=str(rel_dir.relative_to(ROOT)), frames=str(n), sheets=str(sheets),
              release=f"https://github.com/{slug}/releases/tag/{tag}", tag=tag)


if __name__ == "__main__":
    main()
