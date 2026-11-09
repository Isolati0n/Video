"""Stages 6-7: merge per-chunk frame data, derive events, build overview sheets and index.md.

  python -m ytdigest.finalize <video_id> <chunks_dir> <transcript_dir> <workdir>
         [--archive-dir DIR] [--started EPOCH]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

import cv2

from . import events as E
from .index_repo import repo_base
from .common import FPS, digest_dir, hms, load_json, log, save_json
from .sheets import grid, label, save_jpeg, tile_text
from .video import Archive

DEDUP_BITS = int(os.environ.get("SHEET_DEDUP_BITS", "3"))  # 0 disables
MAX_GAP = 10  # seconds
NO_TILE_TYPES = {"text"}  # text events stay in events.md; their 25 fps tiles add little and cost git space
EVENT_COLORS = {"life_drop": (40, 40, 255), "death": (0, 0, 200), "life_refill": (60, 200, 60),
                "mana_refill": (255, 140, 40), "poisoned": (60, 255, 60), "area_change": (0, 220, 255), "off_hud": (128, 128, 128),
                "teleport": (255, 0, 255), "run_burst": (255, 128, 192), "text": (255, 255, 255)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("chunks_dir", type=Path)
    ap.add_argument("transcript_dir", type=Path)
    ap.add_argument("workdir", type=Path)
    ap.add_argument("--archive-dir", type=Path)
    ap.add_argument("--started", type=float, default=time.time())
    a = ap.parse_args()
    vid = a.video_id
    out = digest_dir(vid)
    out.mkdir(parents=True, exist_ok=True)
    info = load_json(a.workdir / "info.json", {}) or {}
    title = info.get("title", vid)

    # --- merge frames: gzip members concatenate into one valid gzip file
    parts = sorted(a.chunks_dir.rglob("frames-*.jsonl.gz"))
    if not parts:
        raise SystemExit("no chunk outputs found")
    merged = out / "frames.jsonl.gz"
    with open(merged, "wb") as fh:
        for p in parts:
            fh.write(p.read_bytes())
    recs = E.load_frames(merged)
    log(f"merged {len(parts)} chunks, {len(recs)} frames")

    # --- events
    ev, stats = E.derive(recs)
    counts = E.write_md(ev, stats, out / "events.md", title)
    save_json(out / "events.json", ev)

    # --- transcript
    for name in ("transcript.md", "transcript.json"):
        src = a.transcript_dir / name
        if src.exists():
            shutil.copy(src, out / name)
    tr = load_json(out / "transcript.json", {"segments": []}) or {"segments": []}
    segs = tr["segments"]

    # --- tiles: one per second + 4 frames at 25 fps around each event
    thumbs = {int(p.stem): p for p in a.chunks_dir.rglob("thumbs/*.jpg")}
    # Static stretches (menus, stash, standing still) repeat near-identical frames. Group them by
    # perceptual hash (idea from watch-for-me's dHash segmentation): skip a 1 fps tile whose hash is
    # within DEDUP_BITS of the last kept tile, but always keep one every MAX_GAP seconds and
    # everything around events, so the timeline stays continuous and git stays small.
    ph = {r["f"] // FPS: r["phash"] for r in recs if r["f"] % FPS == 0}
    near_event = {int(e["t"]) + d for e in ev for d in (-1, 0, 1)}
    kept, last_sec, last_hash, dropped = [], -10**9, None, 0
    for sec in sorted(thumbs):
        h = int(ph[sec], 16) if sec in ph else None
        same = (DEDUP_BITS > 0 and h is not None and last_hash is not None
                and bin(h ^ last_hash).count("1") <= DEDUP_BITS)
        if same and sec not in near_event and sec - last_sec < MAX_GAP:
            dropped += 1
            continue
        kept.append(sec)
        last_sec, last_hash = sec, h
    log(f"1 fps overview tiles: kept {len(kept)}, skipped {dropped} near-identical (hash <= {DEDUP_BITS} bits)")
    tiles = [(float(sec), 0, None, None, thumbs[sec]) for sec in kept]
    arc = Archive(vid, a.workdir, a.archive_dir)
    evdir = a.workdir / "event_frames"
    evdir.mkdir(parents=True, exist_ok=True)
    for e in ev:
        if e["type"] in NO_TILE_TYPES:
            continue
        t0 = (e["f"] - 1) / FPS
        for t, fr in arc.frames(max(0, t0), t0 + 3.5 / FPS, FPS, (640, 360)):
            f = int(round(t * FPS))
            p = evdir / f"{f:08d}.jpg"
            if not p.exists():
                cv2.imwrite(str(p), fr, [cv2.IMWRITE_JPEG_QUALITY, 90])
            tiles.append((t, 1, e, f, p))
    tiles.sort(key=lambda x: (x[0], x[1]))

    sheets_dir = out / "sheets"
    if sheets_dir.exists():
        shutil.rmtree(sheets_dir)
    sheets_dir.mkdir()
    sheet_rows = []
    sizes = []
    for si in range(0, len(tiles), 9):
        batch = tiles[si:si + 9]
        imgs = []
        for t, is_ev, e, f, src in batch:
            img = cv2.imread(str(src)) if isinstance(src, Path) else src
            if img is None:
                continue
            if img.shape[:2] != (360, 640):
                img = cv2.resize(img, (640, 360), interpolation=cv2.INTER_AREA)
            if is_ev:
                c = EVENT_COLORS.get(e["type"], (0, 255, 255))
                img = label(img, tile_text(t, f, e["type"].upper()), (0, 255, 255), c)
            else:
                img = label(img, tile_text(t))
            imgs.append(img)
        if not imgs:
            continue
        name = f"sheet_{si // 9 + 1:04d}.jpg"
        sizes.append(save_jpeg(grid(imgs, 3, 3), sheets_dir / name))
        t_a, t_b = batch[0][0], batch[-1][0]
        evs = sorted({id(x[2]): x[2] for x in batch if x[2]}.values(), key=lambda e: e["t"])
        sheet_rows.append((name, t_a, t_b, evs))

    # --- index.md
    def excerpt(ta, tb, limit=220):
        txt = " ".join(s["text"] for s in segs if s["end"] >= ta and s["start"] <= tb + 1)
        return (txt[:limit] + "...") if len(txt) > limit else txt

    dur = arc.duration
    slug, _, raw = repo_base()
    hdr = [f"# Digest: {title}", "",
           f"Relative links below resolve against `{raw}/digests/{vid}/` (raw URLs).", "",
           f"- Video: {info.get('webpage_url', 'https://youtu.be/' + vid)} ({hms(dur)}), channel: {info.get('channel')}",
           f"- Archive: GitHub Release `{vid}` (720p H.264 parts + parts.json)",
           "- Files: [transcript.md](transcript.md), [events.md](events.md), `events.json`, "
           "`frames.jsonl.gz` (one JSON per frame at 25 fps, keys documented in README), "
           "[calibration.png](calibration.png), `sheets/`",
           f"- Sheets: {len(sheet_rows)} (3x3 tiles of 640x360; white label = 1 fps overview frame, "
           "yellow label + coloured border = one of 4 frames at 25 fps around an event, "
           "label shows HH:MM:SS.ff and frame number f)",
           "- Events: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
           "- Zoom: comment `/zoom " + vid + " H:MM:SS-H:MM:SS [fps]` on the issue for full-detail frames.",
           "", "| sheet | time range | events | transcript |", "|---|---|---|---|"]
    rows = []
    for name, ta, tb, evs in sheet_rows:
        evtxt = "<br>".join(f"{hms(e['t'])} {e['type']}: {e['desc'][:80]}" for e in evs)
        tx = excerpt(ta, tb).replace("|", "/")
        rows.append(f"| [{name}](sheets/{name}) | {hms(ta)}-{hms(tb)} | {evtxt.replace('|', '/')} | {tx} |")
    (out / "index.md").write_text("\n".join(hdr + rows) + "\n")

    runtime = time.time() - a.started
    summary = {"video_id": vid, "title": title, "duration_s": round(dur, 1), "frames": len(recs),
               "events": len(ev), "event_counts": counts, "sheets": len(sheet_rows), "tiles_skipped_static": dropped,
               "max_sheet_bytes": max(sizes) if sizes else 0, "transcript_lines": len(segs),
               "transcript_source": tr.get("source"), "stats": stats, "runtime_s": round(runtime)}
    save_json(out / "summary.json", summary)
    log(json.dumps(summary))


if __name__ == "__main__":
    main()
