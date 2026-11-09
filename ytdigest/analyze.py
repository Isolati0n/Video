"""Stage 5: per-frame analysis of EVERY frame at 25 fps for one time chunk.

  python -m ytdigest.analyze <video_id> <start> <end> <outdir> [--archive-dir DIR] [--workdir DIR]

Writes <outdir>/frames-<start>.jsonl.gz and 1 fps thumbnails <outdir>/thumbs/<sec>.jpg
(used for contact sheets).

Record keys (one JSON object per frame):
  f      frame number at 25 fps (t = f / 25)
  t      seconds
  scene  "game" (HUD visible) | "dark" (black/fade/loading) | "other" (menu, loading art, overlay)
  hud    0..1 similarity of the HUD to the calibrated reference
  life   life % from orb fill (null unless scene == game)
  mana   mana % from orb fill (null unless scene == game)
  poison true when the life orb is green
  motion mean absolute pixel change vs previous frame (0..255, game area only)
  cut    true when the colour histogram jumps (scene change)
  phash  64-bit DCT perceptual hash (hex)
  ocr    {region: text} once per second (chat / center / top_right), only when text found
"""
from __future__ import annotations

import argparse
import gzip
import json
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from . import hud
from .common import FPS, log
from .ocr import ocr_regions
from .video import Archive

SW, SH = 320, 180


def phash(gray_small: np.ndarray) -> str:
    g = cv2.resize(gray_small, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = cv2.dct(g)[:8, :8].flatten()
    bits = d > np.median(d[1:])
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def histo(hsv_small):
    h = cv2.calcHist([hsv_small], [0, 1], None, [18, 8], [0, 180, 0, 256])
    return cv2.normalize(h, h).flatten()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("start", type=float)
    ap.add_argument("end", type=float)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--archive-dir", type=Path)
    ap.add_argument("--workdir", type=Path, default=Path("work"))
    a = ap.parse_args()

    cfg = hud.load(a.video_id)
    if not cfg:
        raise SystemExit("no HUD config; run calibrate first")
    arc = Archive(a.video_id, a.workdir, a.archive_dir)
    W, H = arc.width, arc.height
    reader = hud.HudReader(cfg, W, H)
    ocr_rects = {k: hud.px(cfg.get(k, hud.DEFAULT[k]), W, H) for k in hud.OCR_REGIONS}
    game_rows = int(SH * min(cfg["life"][1], cfg["mana"][1], cfg["skills"][1]))
    game_rows = max(int(SH * 0.5), game_rows)

    a.outdir.mkdir(parents=True, exist_ok=True)
    thumbs = a.outdir / "thumbs"
    thumbs.mkdir(exist_ok=True)
    out_path = a.outdir / f"frames-{int(a.start * FPS):08d}.jsonl.gz"
    pool = ThreadPoolExecutor(max_workers=3)
    pending: deque = deque()
    prev_gray = prev_hist = None
    n = 0
    t0 = time.time()
    # start one frame early so the first frame of the chunk has a motion value
    pre = 1.0 / FPS if a.start > 0 else 0.0
    with gzip.open(out_path, "wt", compresslevel=6) as out:
        def flush(block=False):
            while pending and (block or pending[0][1] is None or pending[0][1].done()):
                rec, fut = pending.popleft()
                if fut is not None:
                    txt = fut.result()
                    if txt:
                        rec["ocr"] = txt
                out.write(json.dumps(rec, separators=(",", ":")) + "\n")

        for t, frame in arc.frames(a.start - pre, a.end):
            k = int(round(t * FPS))
            small = cv2.resize(frame, (SW, SH), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = histo(hsv)
            if prev_gray is None:
                motion, cut = None, False
            else:
                motion = float(cv2.absdiff(gray[:game_rows], prev_gray[:game_rows]).mean())
                cut = bool(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA) > 0.45)
            prev_gray, prev_hist = gray, hist
            if t < a.start - 1e-6:
                continue  # the extra lead-in frame
            score = reader.hud_score(gray)
            dark = gray.mean() < 14
            scene = "dark" if dark else ("game" if score >= 0.45 else "other")
            rec = {"f": k, "t": round(k / FPS, 2), "scene": scene, "hud": round(score, 2)}
            if scene == "game":
                life, mana, poison, _, _ = reader.read(frame)
                rec["life"], rec["mana"] = round(life, 1), round(mana, 1)
                if poison:
                    rec["poison"] = True
            else:
                rec["life"] = rec["mana"] = None
            rec["motion"] = None if motion is None else round(motion, 2)
            if cut:
                rec["cut"] = True
            rec["phash"] = phash(gray)
            fut = None
            if k % FPS == 0:
                sec = k // FPS
                cv2.imwrite(str(thumbs / f"{sec:06d}.jpg"),
                            cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA),
                            [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not dark:
                    crops = {r: frame[y0:y1, x0:x1].copy() for r, (x0, y0, x1, y1) in ocr_rects.items()}
                    fut = pool.submit(ocr_regions, crops)
            pending.append((rec, fut))
            if len(pending) > 400:
                flush(block=len(pending) > 2000)
            else:
                flush()
            n += 1
            if n % (FPS * 300) == 0:
                el = time.time() - t0
                log(f"{t:.0f}s analysed, {n} frames, {n / el:.0f} fps")
        flush(block=True)
    pool.shutdown()
    el = time.time() - t0
    log(f"done: {n} frames in {el:.0f}s ({n / max(el, 1e-6):.0f} fps) -> {out_path}")


if __name__ == "__main__":
    main()
