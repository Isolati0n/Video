"""HUD calibration (detect life orb, mana orb, skill bar) and per-frame orb readers.

Regions are stored per video in config/hud/<video_id>.json as fractions of the frame:
rect = [x, y, w, h] with 0..1 values, so the same file works at any resolution.
Orbs are read inside the ellipse inscribed in their rect.
"""
from __future__ import annotations

import base64

import cv2
import numpy as np

from .common import hud_config_path, load_json

# Fallback layout for D2R at 16:9 (used only when detection fails).
DEFAULT = {
    "life": [0.105, 0.785, 0.115, 0.205],
    "mana": [0.780, 0.785, 0.115, 0.205],
    "skills": [0.30, 0.90, 0.40, 0.10],
    "chat": [0.01, 0.42, 0.46, 0.36],
    "center": [0.28, 0.25, 0.44, 0.25],
    "top_right": [0.74, 0.0, 0.26, 0.20],
}
OCR_REGIONS = ("chat", "center", "top_right")
COLORS = {"life": (40, 40, 230), "mana": (230, 120, 30), "skills": (40, 220, 220),
          "chat": (60, 220, 60), "center": (220, 60, 220), "top_right": (200, 200, 200)}


# ----------------------------------------------------------------- geometry
def px(rect, w, h):
    x, y, rw, rh = rect
    x0, y0 = int(round(x * w)), int(round(y * h))
    x1, y1 = int(round((x + rw) * w)), int(round((y + rh) * h))
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def liquid_masks(hsv: np.ndarray):
    hue, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    sat = (s >= 100) & (v >= 55)
    red = sat & ((hue <= 9) | (hue >= 168))
    green = sat & (hue >= 38) & (hue <= 85)       # poisoned life orb
    blue = sat & (hue >= 98) & (hue <= 132)
    return red, green, blue


class OrbReader:
    """Measures the liquid height inside an orb. Returns raw fill 0..1."""

    def __init__(self, rect, w, h, kind: str):
        self.kind = kind
        self.x0, self.y0, self.x1, self.y1 = px(rect, w, h)
        ow, oh = self.x1 - self.x0, self.y1 - self.y0
        yy, xx = np.mgrid[0:oh, 0:ow]
        cx, cy = (ow - 1) / 2, (oh - 1) / 2
        self.mask = (((xx - cx) / max(1, ow / 2)) ** 2 + ((yy - cy) / max(1, oh / 2)) ** 2) <= 0.92
        self.row_n = np.maximum(1, self.mask.sum(1))
        self.valid_rows = self.row_n >= max(2, 0.2 * ow)

    def read(self, frame_bgr) -> tuple[float, bool]:
        crop = frame_bgr[self.y0:self.y1, self.x0:self.x1]
        if crop.size == 0:
            return 0.0, False
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        red, green, blue = liquid_masks(hsv)
        if self.kind == "mana":
            liq = blue
            poison = False
        else:
            liq = red | green
            poison = bool((green & self.mask).sum() > 1.5 * (red & self.mask).sum() + 10)
        frac = (liq & self.mask).sum(1) / self.row_n
        rows = self.valid_rows
        filled = (frac >= 0.30) & rows
        # liquid grows from the bottom: find the highest row where the run of mostly
        # filled rows (counted from the bottom, tolerating glare gaps) still holds
        n = rows.sum()
        if n == 0:
            return 0.0, poison
        f = filled[rows][::-1]  # bottom -> top
        level, gap = 0, 0
        for i, ok in enumerate(f):
            if ok:
                level, gap = i + 1, 0
            else:
                gap += 1
                if gap > max(2, n // 12):
                    break
        return level / n, poison


class HudReader:
    def __init__(self, cfg: dict, w: int, h: int):
        self.cfg, self.w, self.h = cfg, w, h
        self.life = OrbReader(cfg["life"], w, h, "life")
        self.mana = OrbReader(cfg["mana"], w, h, "mana")
        self.full = cfg.get("full", {"life": 0.95, "mana": 0.95})
        self.ref = None
        if cfg.get("hud_ref"):
            ref = cv2.imdecode(np.frombuffer(base64.b64decode(cfg["hud_ref"]), np.uint8), cv2.IMREAD_UNCHANGED)
            self.ref_gray, self.ref_mask = ref[..., 0], ref[..., 1] > 0
            self.ref = True

    def hud_score(self, small_gray: np.ndarray) -> float:
        """Fraction of stable HUD pixels matching the reference (needs 320x180 gray)."""
        if self.ref is None or self.ref_mask.sum() == 0:
            return 1.0
        d = np.abs(small_gray.astype(np.int16) - self.ref_gray.astype(np.int16))
        return float(((d <= 28) & self.ref_mask).sum() / self.ref_mask.sum())

    def read(self, frame):
        lr, poison = self.life.read(frame)
        mr, _ = self.mana.read(frame)
        life = min(100.0, 100.0 * lr / max(0.3, self.full["life"]))
        mana = min(100.0, 100.0 * mr / max(0.3, self.full["mana"]))
        return life, mana, poison, lr, mr


# ----------------------------------------------------------------- detection
def _component_rect(freq: np.ndarray, region: tuple, thr: float):
    x0, y0, x1, y1 = region
    sub = (freq[y0:y1, x0:x1] >= thr).astype(np.uint8)
    sub = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(sub, 8)
    best, best_score = None, 0
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a < 40 or w < 8 or h < 6:
            continue
        aspect = w / max(1, h)
        if not 0.5 <= aspect <= 3.5:
            continue
        score = a * min(1.0, a / (w * h) / 0.4)
        if score > best_score:
            best, best_score = (x + x0, y + y0, w, h), score
    return best


def detect(frames: list[np.ndarray]) -> tuple[dict, dict]:
    """Detect HUD regions from frames sampled across the video."""
    H, W = frames[0].shape[:2]
    sw, sh = 320, 180
    red_f = np.zeros((sh, sw), np.float32)
    blue_f = np.zeros((sh, sw), np.float32)
    grays = []
    for f in frames:
        small = cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        r, g, b = liquid_masks(hsv)
        red_f += r | g
        blue_f += b
        grays.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    n = len(frames)
    red_f /= n
    blue_f /= n
    notes = {}
    band_top = int(sh * 0.55)
    life = mana = None
    for thr in (0.5, 0.35, 0.2):
        life = life or _component_rect(red_f, (0, band_top, sw // 2, sh), thr)
        mana = mana or _component_rect(blue_f, (sw // 2, band_top, sw, sh), thr)

    def orb_from(c):
        x, y, w, h = c
        d = max(w, h)  # liquid at mid-height spans the full diameter
        cx, bottom = x + w / 2, y + h
        return [cx - d / 2, bottom - d, d, d]

    cfg = {k: list(v) for k, v in DEFAULT.items()}
    lo = orb_from(life) if life else None
    mo = orb_from(mana) if mana else None
    if lo and mo:  # orbs are mirror images: reconcile size and height
        d = max(lo[2], mo[2])
        bottom = max(lo[1] + lo[3], mo[1] + mo[3])
        lcx, mcx = lo[0] + lo[2] / 2, mo[0] + mo[2] / 2
        lo, mo = [lcx - d / 2, bottom - d, d, d], [mcx - d / 2, bottom - d, d, d]
        notes["method"] = "detected both orbs"
    elif lo:
        mo = [sw - lo[0] - lo[2], lo[1], lo[2], lo[3]]
        notes["method"] = "detected life orb, mana mirrored"
    elif mo:
        lo = [sw - mo[0] - mo[2], mo[1], mo[2], mo[3]]
        notes["method"] = "detected mana orb, life mirrored"
    else:
        notes["method"] = "detection failed, using default D2R layout"
    if lo:
        cfg["life"] = [lo[0] / sw, lo[1] / sh, lo[2] / sw, lo[3] / sh]
        cfg["mana"] = [mo[0] / sw, mo[1] / sh, mo[2] / sw, mo[3] / sh]

    # skill bar / HUD panel: temporally stable, textured pixels between the orbs
    stack = np.stack(grays).astype(np.int16)
    med = np.median(stack, 0).astype(np.uint8)
    # robust stability: share of samples close to the median (loading screens, menus and
    # overlays in a few samples must not hide the HUD)
    agree = (np.abs(stack - med.astype(np.int16)) <= 12).mean(0)
    edges = cv2.Canny(med, 40, 120) > 0
    stable = (agree >= 0.6) & (cv2.dilate(edges.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
    lx1 = int((cfg["life"][0] + cfg["life"][2]) * sw)
    mx0 = int(cfg["mana"][0] * sw)
    by0 = int(min(cfg["life"][1], cfg["mana"][1]) * sh)
    if mx0 - lx1 > 20:
        band = stable[by0:, lx1:mx0]
        rows = np.where(band.mean(1) > 0.12)[0]
        cols = np.where(band.mean(0) > 0.12)[0]
        if len(rows) > 3 and len(cols) > 10:
            y0 = by0 + rows.min()
            cfg["skills"] = [(lx1 + cols.min()) / sw, y0 / sh, (cols.max() - cols.min() + 1) / sw,
                             (sh - y0) / sh]
            notes["skills"] = "detected from stable HUD pixels"
        else:
            cfg["skills"] = [lx1 / sw, max(by0, int(0.86 * sh)) / sh, (mx0 - lx1) / sw,
                             1 - max(by0, int(0.86 * sh)) / sh]
            notes["skills"] = "placed between orbs"

    # reference image for 'is the HUD on screen' checks: stable pixels in HUD rects
    hud_mask = np.zeros((sh, sw), bool)
    for k in ("life", "mana", "skills"):
        x0, y0, x1, y1 = px(cfg[k], sw, sh)
        hud_mask[y0:y1, x0:x1] = True
    ref_mask = hud_mask & (agree >= 0.7)  # orb liquid changes, so drop the orb interiors
    for k in ("life", "mana"):
        x0, y0, x1, y1 = px(cfg[k], sw, sh)
        yy, xx = np.mgrid[0:sh, 0:sw]
        inside = (((xx - (x0 + x1) / 2) / max(1, (x1 - x0) / 2)) ** 2 +
                  ((yy - (y0 + y1) / 2) / max(1, (y1 - y0) / 2)) ** 2) < 0.85
        ref_mask &= ~inside
    notes["hud_ref_pixels"] = int(ref_mask.sum())
    ref = np.dstack([med, (ref_mask * 255).astype(np.uint8), np.zeros_like(med)])
    ok, png = cv2.imencode(".png", ref)
    cfg["hud_ref"] = base64.b64encode(png.tobytes()).decode()

    # normalisation: raw fill when the orb is full (players sit at full life most of the time)
    reader = HudReader({**cfg, "hud_ref": None}, W, H)
    lr = np.array([reader.life.read(f)[0] for f in frames])
    mr = np.array([reader.mana.read(f)[0] for f in frames])
    full = {"life": float(np.percentile(lr, 90)), "mana": float(np.percentile(mr, 90))}
    for k in full:
        if full[k] < 0.6:
            notes[f"full_{k}"] = f"p90 raw fill {full[k]:.2f} looked too low; assuming 0.95"
            full[k] = 0.95
    cfg["full"] = full
    notes["samples"] = n
    return cfg, notes


def annotate(frame: np.ndarray, cfg: dict, title: str = "") -> np.ndarray:
    img = frame.copy()
    H, W = img.shape[:2]
    reader = HudReader(cfg, W, H)
    life, mana, poison, lr, mr = reader.read(frame)
    for k, rect in cfg.items():
        if k not in COLORS:
            continue
        x0, y0, x1, y1 = px(rect, W, H)
        c = COLORS[k]
        if k in ("life", "mana"):
            cv2.ellipse(img, (int((x0 + x1) / 2), int((y0 + y1) / 2)), (int((x1 - x0) / 2), int((y1 - y0) / 2)),
                        0, 0, 360, c, 2)
            raw = lr if k == "life" else mr
            ly = int(y1 - raw * (y1 - y0))
            cv2.line(img, (x0, ly), (x1, ly), (255, 255, 255), 1)
            val = life if k == "life" else mana
            label = f"{k} {val:.0f}%" + (" (poison)" if k == "life" and poison else "")
        else:
            label = k
        cv2.rectangle(img, (x0, y0), (x1, y1), c, 2)
        ty = y0 - 6 if y0 > 18 else y1 + 16
        cv2.putText(img, label, (x0 + 2, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, label, (x0 + 2, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
    if title:
        cv2.putText(img, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def load(video_id: str) -> dict | None:
    return load_json(hud_config_path(video_id))


def rect_summary(cfg: dict) -> str:
    rows = []
    for k in ("life", "mana", "skills", *OCR_REGIONS):
        if k in cfg:
            rows.append(f"| {k} | " + ",".join(f"{v * 100:.1f}" for v in cfg[k]) + " |")
    return "| region | x,y,w,h (% of frame) |\n|---|---|\n" + "\n".join(rows)
