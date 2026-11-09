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

# Fallback layout measured on real D2R 16:9 footage (HUD is centred; the orb discs are ~98 px across
# at 720p, at ~22% and ~71% of the width).
DEFAULT = {
    "life": [0.2234, 0.8222, 0.0766, 0.1389],   # 286,592 98x100 px at 1280x720
    "mana": [0.7078, 0.8222, 0.0766, 0.1389],   # 906,592
    "skills": [0.30, 0.87, 0.42, 0.13],
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
    hue, sat_, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    # Liquid stays strongly saturated even where it is shaded dark (S 140-255, V 25-200); the empty
    # part of an orb is a low-saturation brown/grey (S 40-100). Hue then picks red / green / blue.
    sat = (sat_ >= 130) & (val >= 25)
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

    def to_pct(self, kind: str, raw: float) -> float:
        """raw liquid height (0..1) -> value %. Uses the curve fitted against the on-screen numbers
        when calibration found them, else a linear map normalised to the full level."""
        curve = (self.cfg.get("curve") or {}).get(kind)
        if curve and len(curve) >= 2:
            xs, ys = zip(*curve)
            return float(np.interp(raw, xs, ys))
        return min(100.0, 100.0 * raw / max(0.3, self.full[kind]))

    def read(self, frame):
        lr, poison = self.life.read(frame)
        mr, _ = self.mana.read(frame)
        return self.to_pct("life", lr), self.to_pct("mana", mr), poison, lr, mr


# ----------------------------------------------------------------- detection
# Geometry of D2R's HUD at 720p, measured on real footage: each orb has a small white glare spot at a
# fixed place on the sphere, drawn on top of the liquid. The pair of spots gives position and UI scale.
GLARE_SEP = 619.3            # px between the two spots
GLARE_DX, GLARE_DY = 60.7, 31.1   # spot -> top-left corner of the disc's box
ORB_W, ORB_H = 98.0, 100.0


def find_glare(frames: list[np.ndarray]):
    """Stable white-hot spots in the bottom 30% of the frame -> ((x1,y1),(x2,y2),scale) or None."""
    H, W = frames[0].shape[:2]
    acc = np.zeros((H, W), np.float32)
    for f in frames:
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        acc += (hsv[..., 2] >= 235) & (hsv[..., 1] <= 60)
    m = (acc / len(frames) >= 0.6).astype(np.uint8)
    m[:int(H * 0.7)] = 0
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
    blobs = [(cen[i], st[i][4]) for i in range(1, n)
             if 0.00001 * W * H <= st[i][4] <= 0.0004 * W * H and 0.6 <= st[i][2] / max(1, st[i][3]) <= 1.7]
    best = None
    for i, (a, aa) in enumerate(blobs):
        for b, ab in blobs[i + 1:]:
            l, r = (a, b) if a[0] < b[0] else (b, a)
            sep = r[0] - l[0]
            if abs(l[1] - r[1]) > 0.01 * H or not (0.35 * W <= sep <= 0.6 * W):
                continue
            score = min(aa, ab)
            if best is None or score > best[0]:
                best = (score, tuple(l), tuple(r), sep / GLARE_SEP)
    return None if best is None else (best[1], best[2], best[3])


def detect(frames: list[np.ndarray]) -> tuple[dict, dict]:
    """Detect HUD regions from frames sampled across the video."""
    H, W = frames[0].shape[:2]
    sw, sh = 320, 180
    grays = []
    ow, oh = 640, 360  # orb search grid
    red_u = np.zeros((oh, ow), np.float32)
    blue_u = np.zeros((oh, ow), np.float32)
    for f in frames:
        small = cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA)
        grays.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        hsv = cv2.cvtColor(cv2.resize(f, (ow, oh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2HSV)
        r, g, b = liquid_masks(hsv)
        red_u += r | g
        blue_u += b
    n = len(frames)
    notes = {}

    def union_orb(freq, region):
        """Liquid occupies every part of the orb disc at some moment, so the union over time
        (pixels that are liquid in >= ~5% of samples) outlines the whole orb."""
        x0, y0, x1, y1 = region
        m = (freq[y0:y1, x0:x1] >= max(2.0, 0.05 * n)).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
        cnt, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        best = None
        for i in range(1, cnt):
            x, y, w, h, area = stats[i]
            if not (0.05 * ow <= w <= 0.13 * ow and 0.05 * ow <= h <= 0.16 * ow):
                continue
            if area / (w * h) < 0.55:  # a disc fills ~78% of its box
                continue
            if best is None or area > best[4]:
                best = (x + x0, y + y0, w, h, area)
        return best

    glare = find_glare(frames)
    life = mana = None
    if glare is None:
        life = union_orb(red_u, (0, int(oh * 0.6), ow // 2, oh))
        mana = union_orb(blue_u, (ow // 2, int(oh * 0.6), ow, oh))

    def square(c):  # bbox -> square disc rect on the 640x360 grid
        x, y, w, h, _ = c
        d = max(w, h)
        return [x + w / 2 - d / 2, y + h / 2 - d / 2, d, d]

    cfg = {k: list(v) for k, v in DEFAULT.items()}
    lo = square(life) if life else None
    mo = square(mana) if mana else None
    if glare is not None:
        (gx1, gy1), (gx2, gy2), scale = glare
        for key, gx, gy in (("life", gx1, gy1), ("mana", gx2, gy2)):
            cfg[key] = [(gx - GLARE_DX) / W, (gy - GLARE_DY) / H, ORB_W * scale / W, ORB_H * scale / H]
        notes["method"] = f"anchored on the two orb glare spots (UI scale {scale:.2f} x 720p)"
        lo = mo = None
    elif lo and mo:  # same size, same height; keep each orb's own x
        d = max(lo[2], mo[2])
        cy = (lo[1] + lo[3] / 2 + mo[1] + mo[3] / 2) / 2
        lcx, mcx = lo[0] + lo[2] / 2, mo[0] + mo[2] / 2
        lo, mo = [lcx - d / 2, cy - d / 2, d, d], [mcx - d / 2, cy - d / 2, d, d]
        notes["method"] = "detected both orbs from the union of liquid positions"
    elif lo:
        mo = [ow - lo[0] - lo[2], lo[1], lo[2], lo[3]]
        notes["method"] = "detected life orb, mana mirrored"
    elif mo:
        lo = [ow - mo[0] - mo[2], mo[1], mo[2], mo[3]]
        notes["method"] = "detected mana orb, life mirrored"
    else:
        notes["method"] = "orb detection failed, using the default D2R layout"
    if lo:
        cfg["life"] = [lo[0] / ow, lo[1] / oh, lo[2] / ow, lo[3] / oh]
        cfg["mana"] = [mo[0] / ow, mo[1] / oh, mo[2] / ow, mo[3] / oh]

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


def number_box(rect, W, H):
    """Where D2R prints 'Life: cur / max' / 'Mana: cur / max' above an orb."""
    x, y, w, h = rect
    return px([x - 0.12 * w, y - 0.28 * h, 1.27 * w, 0.26 * h], W, H)


def fit_curve(pairs: list[tuple[float, float]]) -> list[list[float]] | None:
    """(raw liquid height 0..1, true value 0..1) pairs -> monotone piecewise-linear curve
    [[raw, pct], ...]: per-bin medians, so a few OCR digit slips do not matter."""
    if len(pairs) < 12:
        return None
    raw = np.array([p[0] for p in pairs])
    val = np.array([p[1] for p in pairs])
    edges = np.linspace(0, 1.0001, 11)
    pts = [(0.0, 0.0)]
    for lo, hi in zip(edges, edges[1:]):
        sel = (raw >= lo) & (raw < hi)
        if sel.sum() >= 2:
            pts.append((float(np.median(raw[sel])), float(np.median(val[sel])) * 100))
    full = raw[val >= 0.985]
    if len(full) >= 3:  # the orb at 100% sits at this raw level; anything above reads 100
        pts = [p for p in pts if p[0] < float(np.percentile(full, 25))] + [(float(np.percentile(full, 50)), 100.0)]
    if len(pts) < 3:
        return None
    xs, ys = zip(*pts)
    ys = np.maximum.accumulate(np.clip(ys, 0, 100))
    return [[round(x, 4), round(float(y), 2)] for x, y in zip(xs, ys)]
