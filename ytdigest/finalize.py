"""Stages 6-7: merge per-chunk data, derive events, choose keyframes, build sheets and the index.

  python -m ytdigest.finalize <video_id> <chunks_dir> <transcript_dir> <workdir>
         [--archive-dir DIR] [--started EPOCH] [--no-upload]

In git (digests/<id>/): index.md, events.md/json, transcript.md/json, ocr.md/json,
frames.jsonl.gz, thumbs/overview_*.jpg (tiny browsable thumbnails), summary.json, meta.json.
In Releases: the contact sheets (tag sheets-<id>, split every 900 files), reachable by URL from index.md.
--no-upload keeps the sheets next to the digest (local testing).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from . import audio, keyframes, profiles, videoconfig
from . import events as E
from .common import GH, digest_dir, hms, load_json, log, save_json
from .index_repo import repo_base
from .sheets import grid, label, save_jpeg, tile_text
from .video import Archive

SHEETS_PER_RELEASE = 900
COLORS = {"scene_change": (0, 220, 255), "motion_burst": (255, 0, 255), "black": (128, 128, 128),
          "loud_peak": (0, 165, 255), "text_change": (255, 255, 255), "static": (200, 200, 0), "silence": (90, 90, 90)}


def sheet_tag(vid: str, i: int) -> str:
    return f"sheets-{vid}" if i == 0 else f"sheets-{vid}-{i + 1}"


def upload_sheets(vid: str, files: list[Path], workers: int = 6) -> None:
    """Fresh release(s) per run so stale sheets never linger; uploads in parallel."""
    gh = GH()
    for i in range(0, (len(files) + SHEETS_PER_RELEASE - 1) // SHEETS_PER_RELEASE or 1):
        gh.delete_release(sheet_tag(vid, i))
    n_rel = (len(files) + SHEETS_PER_RELEASE - 1) // SHEETS_PER_RELEASE
    rels = [gh.ensure_release(sheet_tag(vid, i), f"Contact sheets for {vid}" + (f" ({i + 1})" if i else ""),
                              f"Contact sheets of `{vid}`. Listed with time ranges in digests/{vid}/index.md.")
            for i in range(n_rel)]
    local = {}

    def one(job):
        idx, path = job
        g = getattr(local, "gh", None) or GH()
        local.gh = g
        g.upload_asset(rels[idx // SHEETS_PER_RELEASE], path, "image/jpeg")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, _ in enumerate(ex.map(one, list(enumerate(files))), 1):
            if n % 100 == 0:
                log(f"uploaded {n}/{len(files)} sheets")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("chunks_dir", type=Path)
    ap.add_argument("transcript_dir", type=Path)
    ap.add_argument("workdir", type=Path)
    ap.add_argument("--archive-dir", type=Path)
    ap.add_argument("--started", type=float, default=time.time())
    ap.add_argument("--no-upload", action="store_true")
    a = ap.parse_args()
    vid = a.video_id
    out = digest_dir(vid)
    out.mkdir(parents=True, exist_ok=True)
    info = load_json(a.workdir / "info.json", {}) or {}
    title = info.get("title", vid)
    saved = videoconfig.load(vid) or {}
    prof = profiles.get(saved.get("profile") or "generic")
    cfg = saved.get("config") or {}
    arc = Archive(vid, a.workdir, a.archive_dir)
    fps = profiles.analysis_fps(arc.native_fps, prof)
    log(f"profile={prof.name} fps={fps:g} (native {arc.native_fps:g})")

    # --- merge frames (gzip members concatenate into one valid gzip file) and OCR text
    parts = sorted(a.chunks_dir.rglob("frames-*.jsonl.gz"))
    if not parts:
        raise SystemExit("no chunk outputs found")
    merged = out / "frames.jsonl.gz"
    with open(merged, "wb") as fh:
        for p in parts:
            fh.write(p.read_bytes())
    recs = E.load_frames(merged)
    text = E.load_text(list(a.chunks_dir.rglob("text-*.jsonl.gz")))
    log(f"merged {len(parts)} chunks, {len(recs)} frames, {len(text)} OCR frames with text")

    # --- events: generic core + profile rules
    a_events, a_stats = audio.audio_events(arc, fps)
    gen, gstats = E.derive_generic(recs, fps, text, a_events)
    if prof.generic_event_types is not None:
        gen = [e for e in gen if e["type"] in prof.generic_event_types]
    pev, pstats = prof.derive_events(recs, fps, cfg)
    ev = sorted(gen + pev, key=lambda e: (e["t"], e["type"]))
    stats = {**gstats, **a_stats, **pstats}
    counts = E.write_md(ev, stats, out / "events.md", title, fps, prof.name, prof.events_note())
    save_json(out / "events.json", ev)

    # --- transcript
    for name in ("transcript.md", "transcript.json"):
        src = a.transcript_dir / name
        if src.exists():
            shutil.copy(src, out / name)
    tr = load_json(out / "transcript.json", {"segments": []}) or {"segments": []}
    segs = tr["segments"]

    # --- on-screen text (timestamped, de-duplicated)
    if not text:  # profile-region OCR (e.g. d2r chat / centre / area name) -> same shape
        text = [{"t": r["t"], "f": r["f"], "lines": [{"text": ln, "conf": 0} for v in r["ocr"].values() for ln in v.split(" | ")]}
                for r in recs if r.get("ocr")]
    deduped = E.dedupe_text(text)
    (out / "ocr.md").write_text(
        f"# On-screen text: {title}\n\nWhole-frame OCR about once a second and on every scene change; a line is listed "
        "once, when it first appears. OCR makes mistakes (see README, *What this can and cannot see*).\n\n"
        + "\n".join(f"[{hms(d['t'])}] " + " | ".join(d["new"]) for d in deduped) + "\n")
    save_json(out / "ocr.json", [{"t": d["t"], "f": d["f"], "new": d["new"]} for d in deduped])

    # --- keyframes (scene detection + perceptual-hash dedupe) and dense frames around events
    cands = []
    for p in a.chunks_dir.rglob("thumbs/*.jpg"):
        k = int(p.stem)
        cands.append({"t": k / fps, "f": k, "path": p})
    cands.sort(key=lambda c: c["t"])
    phash_by_f = {r["f"]: r["phash"] for r in recs}
    cut_times = [r["t"] for r in recs if r.get("cut")]
    keys = keyframes.select(cands, phash_by_f, cut_times)
    dense_ev = keyframes.dense_events(ev, prof.dense_types, arc.duration)
    log(f"keyframes: {len(keys)} of {len(cands)} candidates; dense frames around {len(dense_ev)} of {len(ev)} events")
    evdir = a.workdir / "event_frames"
    evdir.mkdir(parents=True, exist_ok=True)
    tiles = [(c["t"], 0, None, c["f"], c["path"]) for c in keys]
    for e in dense_ev:
        t0 = max(0.0, (e["f"] - 1) / fps)
        for t, fr in arc.frames(t0, t0 + 3.5 / fps, fps, (640, 360)):
            f = int(round(t * fps))
            p = evdir / f"{f:09d}.jpg"
            if not p.exists():
                cv2.imwrite(str(p), fr, [cv2.IMWRITE_JPEG_QUALITY, 90])
            tiles.append((t, 1, e, f, p))
    tiles.sort(key=lambda x: (x[0], x[1]))

    # --- contact sheets: 3x3 tiles of 640x360
    sheets_dir = a.workdir / "sheets_out"
    if sheets_dir.exists():
        shutil.rmtree(sheets_dir)
    sheets_dir.mkdir()
    sheet_rows, sizes, files = [], [], []
    colors = {**COLORS, **prof.colors}
    for si in range(0, len(tiles), 9):
        batch = tiles[si:si + 9]
        imgs = []
        for t, is_ev, e, f, src in batch:
            img = cv2.imread(str(src))
            if img is None:
                continue
            if img.shape[:2] != (360, 640):
                img = cv2.resize(img, (640, 360), interpolation=cv2.INTER_AREA)
            if is_ev:
                img = label(img, tile_text(t, f, e["type"].upper(), fps), (0, 255, 255), colors.get(e["type"], (0, 255, 255)))
            else:
                img = label(img, tile_text(t))
            imgs.append(img)
        if not imgs:
            continue
        name = f"sheet_{si // 9 + 1:04d}.jpg"
        sizes.append(save_jpeg(grid(imgs, 3, 3), sheets_dir / name))
        files.append(sheets_dir / name)
        sheet_rows.append((name, batch[0][0], batch[-1][0]))

    # --- tiny thumbnails that stay in git (regular keyframes only)
    tdir = out / "thumbs"
    if tdir.exists():
        shutil.rmtree(tdir)
    tdir.mkdir()
    n_overview = 0
    small = []
    for c in keys:
        img = cv2.imread(str(c["path"]))
        if img is not None:
            small.append(label(cv2.resize(img, (192, 108), interpolation=cv2.INTER_AREA), hms(c["t"])[3:], min_scale=0.4))
    for i in range(0, len(small), 80):
        n_overview += 1
        save_jpeg(grid(small[i:i + 80], 10, 8), tdir / f"overview_{n_overview:03d}.jpg", qualities=(45, 38))

    # --- upload sheets (or keep them local)
    slug, _, raw = repo_base()
    if a.no_upload or not os.environ.get("GITHUB_TOKEN"):
        dest = out / "sheets"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(sheets_dir, dest)
        sheet_url = lambda name: f"sheets/{name}"  # noqa: E731
        sheets_where = "`sheets/` next to this file (local run)"
    else:
        upload_sheets(vid, files)
        sheet_url = lambda name: f"https://github.com/{slug}/releases/download/{sheet_tag(vid, (int(name[6:10]) - 1) // SHEETS_PER_RELEASE)}/{name}"  # noqa: E731
        sheets_where = f"Release `sheets-{vid}`: `https://github.com/{slug}/releases/download/sheets-{vid}/sheet_NNNN.jpg`"

    # --- index.md
    def excerpt(ta, tb, limit=220):
        txt = " ".join(s["text"] for s in segs if s["end"] >= ta and s["start"] <= tb + 1)
        return (txt[:limit] + "...") if len(txt) > limit else txt

    def ocr_excerpt(ta, tb, limit=160):
        txt = " | ".join(x for d in deduped if ta <= d["t"] <= tb for x in d["new"])
        return (txt[:limit] + "...") if len(txt) > limit else txt

    dur = arc.duration
    src_line = info.get("webpage_url") or "(uploaded / ingested file)"
    hdr = [f"# Digest: {title}", "",
           f"Relative links below resolve against `{raw}/digests/{vid}/` (raw URLs).", "",
           f"- Video: {src_line} ({hms(dur)}), channel: {info.get('channel')}",
           f"- Profile: `{prof.name}` ({prof.title}); analysed at {fps:g} fps (video is {arc.native_fps:g} fps)",
           f"- Archive: GitHub Release `{vid}` (720p H.264 parts + parts.json)",
           "- Files: [transcript.md](transcript.md), [ocr.md](ocr.md) (on-screen text), [events.md](events.md), `events.json`, "
           "`frames.jsonl.gz` (one JSON per frame), `thumbs/overview_*.jpg` (tiny thumbnails of every keyframe)"
           + (", [calibration.png](calibration.png)" if (out / "calibration.png").exists() else ""),
           f"- Sheets: {len(sheet_rows)} contact sheets (3x3 tiles of 640x360; white label = keyframe chosen by scene "
           "change / picture change, coloured border + yellow label = dense frames around an event with HH:MM:SS.ff and frame "
           f"number f). Stored in {sheets_where}",
           "- Events: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
           "- Zoom: comment `/zoom " + vid + " H:MM:SS-H:MM:SS [fps]` on the issue for every frame of a range (native rate by default).",
           "", "| sheet | time range | events | on-screen text | transcript |", "|---|---|---|---|---|"]
    rows = []
    for name, ta, tb in sheet_rows:
        evs = [e for e in ev if ta <= e["t"] <= tb and e["type"] != "text_change"][:5]
        evtxt = "<br>".join(f"{hms(e['t'])} {e['type']}: {e['desc'][:70]}" for e in evs).replace("|", "/")
        rows.append(f"| [{name}]({sheet_url(name)}) | {hms(ta)}-{hms(tb)} | {evtxt} | "
                    f"{ocr_excerpt(ta, tb).replace('|', '/')} | {excerpt(ta, tb).replace('|', '/')} |")
    (out / "index.md").write_text("\n".join(hdr + rows) + "\n")

    git_bytes = sum(p.stat().st_size for p in out.rglob("*") if p.is_file() and "sheets" not in p.relative_to(out).parts)
    runtime = time.time() - a.started
    event_sources: dict[str, int] = {}
    for e in ev:
        event_sources[e.get("source", "?")] = event_sources.get(e.get("source", "?"), 0) + 1
    summary = {"video_id": vid, "title": title, "profile": prof.name, "fps": fps, "native_fps": arc.native_fps,
               "duration_s": round(dur, 1), "frames": len(recs), "events": len(ev), "event_counts": counts,
               "events_by_source": event_sources, "keyframes": len(keys), "keyframe_candidates": len(cands),
               "dense_events": len(dense_ev), "sheets": len(sheet_rows), "max_sheet_bytes": max(sizes) if sizes else 0,
               "sheet_bytes_total": sum(sizes), "git_bytes": git_bytes, "transcript_lines": len(segs),
               "transcript_source": tr.get("source"), "ocr_entries": len(deduped), "stats": stats, "runtime_s": round(runtime)}
    save_json(out / "summary.json", summary)
    save_json(out / "meta.json", {"video_id": vid, "profile": prof.name, "fps": fps, "native_fps": arc.native_fps,
                                  "width": arc.width, "height": arc.height, "duration_s": round(dur, 3),
                                  "sheets": len(sheet_rows), "sheet_tags": [sheet_tag(vid, i) for i in range((len(files) + SHEETS_PER_RELEASE - 1) // SHEETS_PER_RELEASE)]})
    log(json.dumps(summary))


if __name__ == "__main__":
    main()
