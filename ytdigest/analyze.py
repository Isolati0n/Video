"""Stage 5: per-frame analysis of EVERY frame at the analysis rate, for one time chunk.

  python -m ytdigest.analyze <video_id> <start> <end> <outdir> [--archive-dir DIR] [--workdir DIR]
         [--profile NAME]

The rate is the video's native fps (capped at 60), or the profile's own if it sets one.

Writes into <outdir>:
  frames-<k0>.jsonl.gz   one JSON record per frame (generic fields below, plus the profile's extras)
  text-<k0>.jsonl.gz     whole-frame OCR: {"t", "f", "lines": [{"text", "conf", "x", "y"}]}  (x, y: permille of the frame)
  thumbs/<frame>.jpg     640x360 candidates for the contact sheets (changed picture, every 10 s, or an OCR frame)

Generic record keys:
  f       frame number at the analysis rate (t = f / fps)
  t       seconds
  motion  mean absolute pixel change vs the previous frame (0..255, whole frame, 320x180 gray)
  cut     true when the colour histogram jumps (a scene change)
  phash   64-bit DCT perceptual hash (hex)
  bright  mean brightness 0..255
  sig     coarse scene signature: 2x2 grid of (hue bin 0-7, saturation 0/1, value bin 0-3), 6 hex chars
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

from . import pip, profiles, videoconfig
from .common import log
from .ocr import ocr_frame
from .video import Archive

SW, SH = 320, 180
THUMB_MIN_BITS = 3     # a thumbnail candidate is kept when the picture changed by >= this many hash bits ...
THUMB_MAX_GAP = 10     # ... or when this many seconds passed since the last one
OCR_MIN_GAP = 0.5      # seconds between OCR runs (scene changes may OCR sooner than the 1 s rhythm)
OCR_BLOCK_DIFF = 3.0   # a block (8x8 at 320x180) counts as changed above this mean difference


def phash(gray_small: np.ndarray) -> str:
    g = cv2.resize(gray_small, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = cv2.dct(g)[:8, :8].flatten()
    bits = d > np.median(d[1:])
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def histo(hsv_small):
    h = cv2.calcHist([hsv_small], [0, 1], None, [18, 8], [0, 180, 0, 256])
    return cv2.normalize(h, h).flatten()


def signature(small_bgr: np.ndarray) -> str:
    tiny = cv2.cvtColor(cv2.resize(small_bgr, (2, 2), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    v = 0
    for h, s, val in tiny:
        v = (v << 6) | ((int(h) * 8 // 180) << 3) | ((1 if s >= 64 else 0) << 2) | (int(val) // 64)
    return f"{v:06x}"


def changed_blocks(a: np.ndarray, b: np.ndarray) -> int:
    d = cv2.absdiff(a, b)
    bh, bw = d.shape[0] // 8 * 8, d.shape[1] // 8 * 8
    return int((d[:bh, :bw].reshape(bh // 8, 8, bw // 8, 8).mean((1, 3)) > OCR_BLOCK_DIFF).sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("start", type=float)
    ap.add_argument("end", type=float)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--archive-dir", type=Path)
    ap.add_argument("--workdir", type=Path, default=Path("work"))
    ap.add_argument("--profile", default="")
    a = ap.parse_args()

    saved = videoconfig.load(a.video_id) or {}
    prof = profiles.get(a.profile or saved.get("profile") or "generic")
    cfg = saved.get("config") or {}
    arc = Archive(a.video_id, a.workdir, a.archive_dir)
    hmask = saved.get("hash_mask")
    mx0, my0, mx1, my1 = (None,) * 4
    if hmask:   # webcam / picture-in-picture: ignored by the generic picture-change measures
        mx0, my0, mx1, my1 = pip.mask_rect(hmask["rect"], SW, SH)
    fps = profiles.analysis_fps(arc.native_fps, prof)
    fps_i = max(1, int(round(fps)))
    if type(prof).analyzer is not profiles.Profile.analyzer and not cfg:
        raise SystemExit(f"profile {prof.name} has no calibration for this video; run calibrate first")
    an = prof.analyzer(cfg, arc.width, arc.height, fps)
    log(f"profile={prof.name} fps={fps:g} (native {arc.native_fps:g}) frames {a.start:.0f}s-{a.end:.0f}s")

    a.outdir.mkdir(parents=True, exist_ok=True)
    thumbs = a.outdir / "thumbs"
    thumbs.mkdir(exist_ok=True)
    k0 = int(round(a.start * fps))
    out_path = a.outdir / f"frames-{k0:09d}.jsonl.gz"
    text_path = a.outdir / f"text-{k0:09d}.jsonl.gz"
    pool = ThreadPoolExecutor(max_workers=3)
    pending: deque = deque()   # (rec, profile-ocr future | None)
    text_pending: deque = deque()   # (t, k, future)
    prev_gray = prev_hist = None
    last_sec = -1
    last_thumb_hash, last_thumb_sec, cut_since_thumb = None, -10**9, False
    last_ocr_gray, last_ocr_t = None, -10**9
    n = 0
    t0 = time.time()
    pre = 1.0 / fps if a.start > 0 else 0.0   # one lead-in frame so the first frame has a motion value
    with gzip.open(out_path, "wt", compresslevel=6) as out, gzip.open(text_path, "wt", compresslevel=6) as tout:
        def flush(block=False):
            while pending and (block or pending[0][1] is None or pending[0][1].done()):
                rec, fut = pending.popleft()
                if fut is not None:
                    txt = fut.result()
                    if txt:
                        rec["ocr"] = txt
                out.write(json.dumps(rec, separators=(",", ":")) + "\n")
            while text_pending and (block or text_pending[0][2].done()):
                tt, kk, fut = text_pending.popleft()
                lines = fut.result()
                if lines:
                    tout.write(json.dumps({"t": round(tt, 3), "f": kk,
                                           "lines": [{"text": x["text"], "conf": x["conf"], "x": x["x"], "y": x["y"]} for x in lines]},
                                          separators=(",", ":")) + "\n")

        for t, frame in arc.frames(a.start - pre, a.end, fps):
            k = int(round(t * fps))
            small = cv2.resize(frame, (SW, SH), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)   # what profiles get (unmasked)
            if mx0 is not None:
                gsmall = small.copy()
                gsmall[my0:my1, mx0:mx1] = 128
                ggray = cv2.cvtColor(gsmall, cv2.COLOR_BGR2GRAY)
            else:
                gsmall, ggray = small, gray
            hsv = cv2.cvtColor(gsmall, cv2.COLOR_BGR2HSV)
            hist = histo(hsv)
            if prev_gray is None:
                motion, cut = None, False
            else:
                motion = float(cv2.absdiff(ggray, prev_gray).mean())
                cut = bool(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA) > 0.45)
            prev_gray, prev_hist = ggray, hist
            rec = {"f": k, "t": round(k / fps, 3)}
            if an is not None:
                an.process(frame, gray, k, t, rec)   # profile extras (also on the lead-in frame, to keep its state)
            if t < a.start - 1e-6:
                continue
            bright = float(gray.mean())
            rec["motion"] = None if motion is None else round(motion, 2)
            if cut:
                rec["cut"] = True
                cut_since_thumb = True
            ph = phash(ggray)
            rec["phash"] = ph
            rec["bright"] = round(bright, 1)
            rec["sig"] = signature(gsmall)

            fut = None
            crops = rec.pop("_ocr_crops", None)
            if crops is not None and an is not None and an.ocr_fn is not None:
                fut = pool.submit(an.ocr_fn, crops)

            sec = int(t + 1e-6)
            new_sec = sec != last_sec
            if new_sec:
                last_sec = sec
                h = int(ph, 16)
                dist = 64 if last_thumb_hash is None else bin(h ^ last_thumb_hash).count("1")
                if dist >= THUMB_MIN_BITS or cut_since_thumb or sec - last_thumb_sec >= THUMB_MAX_GAP:
                    cv2.imwrite(str(thumbs / f"{k:09d}.jpg"), cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA),
                                [cv2.IMWRITE_JPEG_QUALITY, 88])
                    last_thumb_hash, last_thumb_sec, cut_since_thumb = h, sec, False
            if prof.generic_ocr and bright >= 14 and (new_sec or cut) and t - last_ocr_t >= OCR_MIN_GAP:
                if last_ocr_gray is None or changed_blocks(ggray, last_ocr_gray) >= 1:
                    text_pending.append((t, k, pool.submit(ocr_frame, frame.copy())))
                    tp = thumbs / f"{k:09d}.jpg"      # a text-driven keyframe needs an image at this frame
                    if not tp.exists():
                        cv2.imwrite(str(tp), cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA),
                                    [cv2.IMWRITE_JPEG_QUALITY, 88])
                    last_ocr_gray, last_ocr_t = ggray, t
            pending.append((rec, fut))
            if len(pending) > 400:
                flush(block=len(pending) > 2000)
            else:
                flush()
            n += 1
            if n % (fps_i * 300) == 0:
                el = time.time() - t0
                log(f"{t:.0f}s analysed, {n} frames, {n / el:.0f} fps")
        flush(block=True)
    pool.shutdown()
    el = time.time() - t0
    log(f"done: {n} frames in {el:.0f}s ({n / max(el, 1e-6):.0f} fps) -> {out_path}")


if __name__ == "__main__":
    main()
