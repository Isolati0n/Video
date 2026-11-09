"""Diablo II: Resurrected profile (PvP footage).

Everything D2R-specific lives here: HUD calibration (life/mana orbs, skill bar, text regions),
per-frame life %, mana %, poison and camera tracking, and the event rules (life drops, potion
refills, poison, death, area changes, teleports / view jumps, fast movement).

D2R runs its simulation at 25 fps, so this profile analyses at 25 fps whatever the video's rate.

Regions are stored per video (config/videos/<id>.json -> "config") as fractions of the frame:
rect = [x, y, w, h] with 0..1 values, so the same file works at any resolution.
Orbs are read inside the ellipse inscribed in their rect.
"""
from __future__ import annotations

import base64
import re
from collections import Counter

import cv2
import numpy as np
import pytesseract

from ..common import hms, log
from . import Analyzer, Calibration, Profile

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


# =============================================================================== on-screen numbers
NUM_RE = re.compile(r"(\d{1,5})\s*/\s*(\d{1,5})")


def read_orb_numbers(crop: np.ndarray) -> tuple[int, int] | None:
    """'Life: 1551 / 7679' / 'Mana: 582 / 852' printed above the orbs (when the game shows them)
    -> (current, maximum), or None. Used to calibrate and verify the orb readings.
    Two thresholds are tried (fixed, then Otsu) because the text colour differs between setups."""
    if crop.size == 0:
        return None
    g = cv2.resize(crop.max(axis=2), None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    candidates = [cv2.threshold(g, 150, 255, cv2.THRESH_BINARY)[1],
                  cv2.threshold(g, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]]
    for bw in candidates:
        try:
            txt = pytesseract.image_to_string(
                255 - bw, config="--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789/:LifeMana ", timeout=3)
        except Exception:
            return None
        m = NUM_RE.search(txt)
        if m:
            cur, mx = int(m.group(1)), int(m.group(2))
            if 0 <= cur <= mx and mx > 0:
                return cur, mx
    return None




# =============================================================================== calibration
def apply_sets(cfg: dict, sets: list[str]) -> list[str]:
    changed = []
    for item in sets or []:
        k, _, v = item.partition("=")
        k = k.strip().lower()
        if k.startswith("full_"):
            cfg.setdefault("full", {})[k[5:]] = float(v)
        else:
            vals = [float(x) / 100 for x in v.replace(" ", "").split(",")]
            if len(vals) != 4:
                raise SystemExit(f"{k}: need x,y,w,h in percent, got {v!r}")
            cfg[k] = vals
        changed.append(k)
    return changed


def orb_numbers_check(arc, cfg: dict, fps: float, n: int = 80) -> None:
    """Some D2R setups print 'Life: cur / max' above the orbs. If they are on screen, use them as
    ground truth: fit the raw-height -> value curve on half of the samples and report the error on
    the other half (held-out), then refit on all of them."""
    W, H = arc.width, arc.height
    reader = HudReader({**cfg, "curve": None, "hud_ref": None}, W, H)
    d = arc.duration
    times = np.linspace(0.04 * d, 0.96 * d, n) if d > 60 else np.linspace(0, max(0.1, d - 0.5), n)
    rows = {"life": [], "mana": []}
    for t in times:
        f = arc.frame_at(float(t), fps=fps)
        if f is None:
            continue
        raw = {"life": reader.life.read(f)[0], "mana": reader.mana.read(f)[0]}
        for kind in rows:
            x0, y0, x1, y1 = number_box(cfg[kind], W, H)
            got = read_orb_numbers(f[y0:y1, x0:x1])
            if got:
                rows[kind].append((raw[kind], got[0], got[1]))
    notes, curves = cfg.setdefault("notes", {}), {}
    check = {}
    for kind, r in rows.items():
        if len(r) < 12:
            check[kind] = f"on-screen numbers not found ({len(r)} readable samples); linear fill used"
            continue
        # a digit slip can fake a different maximum: keep readings whose max is one of the 2 commonest
        maxes = [m for _, _, m in r]
        top = [v for v, _ in Counter(maxes).most_common(2)]
        pairs = [(raw, cur / mx) for raw, cur, mx in r if mx in top]
        fit, test = pairs[0::2], pairs[1::2]
        curve = fit_curve(fit)
        if curve is None:
            check[kind] = f"too few usable number readings ({len(pairs)}); linear fill used"
            continue
        xs, ys = zip(*curve)
        err = np.array([abs(np.interp(raw, xs, ys) - 100 * v) for raw, v in test])
        lin = np.array([abs(min(100.0, 100 * raw / 0.95) - 100 * v) for raw, v in test])
        check[kind] = {"samples": len(pairs), "held_out_mae_pct": round(float(err.mean()), 2),
                       "held_out_median_abs_err_pct": round(float(np.median(err)), 2),
                       "held_out_p90_err_pct": round(float(np.percentile(err, 90)), 2),
                       "linear_mae_pct": round(float(lin.mean()), 2)}
        curves[kind] = fit_curve(pairs)
    if curves:
        cfg["curve"] = curves
    notes["orb_vs_on_screen_numbers"] = check
    log(f"orb check: {check}")


def calibration_comment(vid: str, cfg: dict) -> str:
    notes = cfg.get("notes", {})
    chk = notes.get("orb_vs_on_screen_numbers") or {}
    lines = []
    for kind, v in chk.items():
        if isinstance(v, dict):
            lines.append(f"- {kind}: median error {v['held_out_median_abs_err_pct']}%, mean {v['held_out_mae_pct']}%, "
                         f"90th pct {v['held_out_p90_err_pct']}% vs the game's own on-screen numbers "
                         f"({v['samples']} held-out-tested samples; a plain linear map would be {v['linear_mae_pct']}% mean)")
        else:
            lines.append(f"- {kind}: {v}")
    check_txt = ("\n\n**Orb readings vs the on-screen `Life: x / y` numbers:**\n" + "\n".join(lines)) if lines else ""
    return (f"Red ellipse = life orb, blue = mana orb, yellow = skill bar, other boxes = OCR text areas. "
            f"The white line in each orb is the measured fill level.\n\n{rect_summary(cfg)}{check_txt}\n\n"
            f"Analysis continues with these regions. If they are wrong, reply with e.g.\n"
            f"`/hud {vid} life=5,79,11,20 mana=84,79,11,20` (x,y,w,h in % of the frame) "
            f"or `/recalibrate {vid}`; the analysis then re-runs from the archived video "
            f"(no re-download).")


# =============================================================================== frame analyzer
class D2RAnalyzer(Analyzer):
    """Per-frame life/mana/poison, game-area motion and camera tracking (shift/match/chg)."""

    def __init__(self, cfg: dict, W: int, H: int, fps: float):
        self.cfg, self.fps = cfg, int(round(fps))
        self.reader = HudReader(cfg, W, H)
        self.ocr_rects = {k: px(cfg.get(k, DEFAULT[k]), W, H) for k in OCR_REGIONS}
        sh, sw = 180, 320
        game_rows = int(sh * min(cfg["life"][1], cfg["mana"][1], cfg["skills"][1]))
        self.game_rows = max(int(sh * 0.5), game_rows)
        # central game area for camera tracking: excludes the party list, FPS/ping text and top-right game info
        self.cx0, self.cx1, self.cy0, self.cy1 = int(0.10 * sw), int(0.72 * sw), int(0.12 * sh), self.game_rows
        self.win = cv2.createHanningWindow((self.cx1 - self.cx0, self.cy1 - self.cy0), cv2.CV_32F)
        self.prev = None
        from ..ocr import ocr_regions
        self.ocr_fn = ocr_regions

    def process(self, frame, gray, k, t, rec):
        gr, cx0, cx1, cy0, cy1 = self.game_rows, self.cx0, self.cx1, self.cy0, self.cy1
        prev = self.prev
        if prev is None:
            motion = shift = match = chg = None
        else:
            motion = float(cv2.absdiff(gray[:gr], prev[:gr]).mean())
            # Camera tracking by phase correlation on the central game area:
            #   shift = global translation in px at 320 wide (~0 standing, 1-3 running)
            #   match = how well the two frames correlate (0..1). A spell flash changes `motion`
            #           but keeps match ~1; a teleport shows a new view: motion high, match low.
            (dx, dy), match = cv2.phaseCorrelate(prev[cy0:cy1, cx0:cx1].astype(np.float32),
                                                 gray[cy0:cy1, cx0:cx1].astype(np.float32), self.win)
            shift = float(np.hypot(dx, dy))
            # share of 8x8 blocks of the central game area that changed: a new view changes almost
            # all of them, a UI panel or a spell effect only some
            d = cv2.absdiff(gray[cy0:cy1, cx0:cx1], prev[cy0:cy1, cx0:cx1])
            bh, bw = d.shape[0] // 8 * 8, d.shape[1] // 8 * 8
            chg = float((d[:bh, :bw].reshape(bh // 8, 8, bw // 8, 8).mean((1, 3)) > 4).mean())
        self.prev = gray
        score = self.reader.hud_score(gray)
        dark = gray.mean() < 14
        scene = "dark" if dark else ("game" if score >= 0.45 else "other")
        rec["scene"], rec["hud"] = scene, round(score, 2)
        if scene == "game":
            life, mana, poison, _, _ = self.reader.read(frame)
            rec["life"], rec["mana"] = round(life, 1), round(mana, 1)
            if poison:
                rec["poison"] = True
        else:
            rec["life"] = rec["mana"] = None
        rec["motion_game"] = None if motion is None else round(motion, 2)
        rec["shift"] = None if shift is None else round(shift, 1)
        rec["match"] = None if match is None else round(float(match), 2)
        rec["chg"] = None if chg is None else round(chg, 2)
        if k % self.fps == 0 and not dark:
            rec["_ocr_crops"] = {r: frame[y0:y1, x0:x1].copy() for r, (x0, y0, x1, y1) in self.ocr_rects.items()}


# =============================================================================== events
P = {  # thresholds (life/mana in % points; shift in px at 320 wide)
    "drop_pts": 10, "drop_window": 1.0,        # tuned on real footage: biggest 1 s fall in a 5 min PvP clip was 19 pts
    "refill_pts": 12, "refill_window": 0.6,
    "death_life": 3, "death_hold": 0.4,
    "teleport_motion": 5.0, "teleport_match": 0.2, "teleport_chg": 0.6, "teleport_gap": 1.5,  # new view in one frame; chains merge within the gap
    "run_px": 1.2, "run_min_dur": 0.8,         # sustained camera shift per frame (walking is ~0.2-0.5 here)
}


def _medfilt(x: np.ndarray, k: int = 5) -> np.ndarray:
    """nan-aware running median."""
    pad = k // 2
    xp = np.pad(x, pad, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(xp, k)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return np.nanmedian(win, axis=1)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def derive(recs: list[dict], fps: float = 25) -> tuple[list[dict], dict]:
    FPS = int(round(fps))
    n = len(recs)
    if n == 0:
        return [], {}
    t = np.array([r["t"] for r in recs])
    game = np.array([r["scene"] == "game" for r in recs])
    life = np.array([np.nan if r.get("life") is None else r["life"] for r in recs], float)
    mana = np.array([np.nan if r.get("mana") is None else r["mana"] for r in recs], float)
    L, M = _medfilt(life, 5), _medfilt(mana, 5)
    L[~game], M[~game] = np.nan, np.nan
    ev: list[dict] = []

    def add(i, typ, desc, **kw):
        ev.append({"t": round(float(t[i]), 2), "f": recs[i]["f"], "type": typ, "desc": desc, **kw})

    # --- life drops and refills (only across uninterrupted gameplay frames)
    def extend(x, k, sign):
        """Follow a fall (sign=-1) or rise (+1) while it continues within 0.25 s steps."""
        look = max(2, FPS // 4)
        while k + 1 < n:
            win = x[k + 1:k + 1 + look]
            if np.isnan(win).any():
                break
            j = int(np.argmin(win) if sign < 0 else np.argmax(win))
            if sign * (win[j] - x[k]) < 1.0:
                break
            k = k + 1 + j
        return k

    def changes(x, name):
        w_drop = int(P["drop_window"] * FPS)
        w_ref = int(P["refill_window"] * FPS)
        i = max(w_drop, w_ref)
        while i < n:
            if np.isnan(x[i]):
                i += 1
                continue
            seg = x[i - w_drop:i + 1]
            if name == "life" and not np.isnan(seg).any() and seg.max() - x[i] >= P["drop_pts"]:
                j = i - w_drop + int(np.flatnonzero(seg == seg.max())[-1])
                k = extend(x, i, -1)
                frm, to = x[j], x[k]
                add(j, "life_drop", f"Life drop -{frm - to:.0f}% ({frm:.0f}% -> {to:.0f}%) in {t[k] - t[j]:.2f}s",
                    end=round(float(t[k]), 2), amount=round(float(frm - to), 1))
                i = k + max(w_drop, w_ref) + 1  # window must clear the event
                continue
            seg = x[i - w_ref:i + 1]
            if not np.isnan(seg).any() and x[i] - seg.min() >= P["refill_pts"]:
                j = i - w_ref + int(np.flatnonzero(seg == seg.min())[-1])
                k = extend(x, i, +1)
                frm, to = x[j], x[k]
                what = "rejuvenation potion?" if name == "life" else "mana/rejuv potion?"
                add(j, f"{name}_refill", f"{name.capitalize()} refill +{to - frm:.0f}% ({frm:.0f}% -> {to:.0f}%) "
                    f"in {t[k] - t[j]:.2f}s ({what})", end=round(float(t[k]), 2), amount=round(float(to - frm), 1))
                i = k + max(w_drop, w_ref) + 1  # window must clear the event
                continue
            i += 1

    changes(L, "life")
    changes(M, "mana")

    # --- deaths: empty life orb held while HUD visible, or death text
    hold = int(P["death_hold"] * FPS)
    last_death = -1e9
    low = (L <= P["death_life"]) & game
    run = 0
    for i in range(n):
        run = run + 1 if low[i] else 0
        if run == hold and t[i] - last_death > 10:
            j = i - hold + 1
            add(j, "death", "Death? life orb empty for >= %.1fs" % P["death_hold"])
            last_death = t[j]
    death_re = re.compile(r"(you have died|has been slain|was slain|slain by|killed by|died)", re.I)
    for i, r in enumerate(recs):
        for reg, txt in (r.get("ocr") or {}).items():
            if death_re.search(txt) and t[i] - last_death > 10:
                add(i, "death", f"Death text ({reg}): {txt[:120]}")
                last_death = t[i]

    # --- poison onsets (green life orb for >= 0.4 s)
    pois = np.array([bool(r.get("poison")) for r in recs])
    run = 0
    for i in range(n):
        run = run + 1 if pois[i] else 0
        if run == hold:
            k = i
            while k + 1 < n and pois[k + 1]:
                k += 1
            add(i - hold + 1, "poisoned", f"Poisoned (green life orb) for {t[k] - t[i - hold + 1] + 1 / FPS:.1f}s",
                end=round(float(t[k]), 2))

    # --- area changes / off-HUD segments
    i = 0
    while i < n:
        if game[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and not game[j + 1]:
            j += 1
        dur = t[j] - t[i] + 1 / FPS
        kinds = {recs[k]["scene"] for k in range(i, j + 1)}
        had_cut = any(recs[k].get("cut") for k in range(max(0, i - 2), min(n, j + 3)))
        if i > 0 and j < n - 1 and 0.3 <= dur <= 30 and (had_cut or "dark" in kinds):
            add(i, "area_change", f"Area change / loading: HUD gone {dur:.1f}s ({'+'.join(sorted(kinds))})",
                end=round(float(t[j]), 2))
        elif dur > 30:
            add(i, "off_hud", f"No HUD for {dur:.0f}s ({'+'.join(sorted(kinds))}: menu, stash, overlay or non-game footage)",
                end=round(float(t[j]), 2))
        i = j + 1

    # --- teleports and sustained fast movement (whirlwind / charge / run).
    # Verified on real footage: a teleport is a frame where the whole view changes (motion high, match
    # LOW because the new view shares nothing with the old one, and nearly ALL blocks of the game area
    # change); a spell flash changes `motion` but keeps match ~1; a UI panel opening/closing is a
    # well-matched rigid shift (match 0.3-0.6); a local spell effect changes only some blocks.
    shift = np.array([np.nan if r.get("shift") is None else r["shift"] for r in recs], float)
    match = np.array([np.nan if r.get("match") is None else r["match"] for r in recs], float)
    mot = np.array([np.nan if r.get("motion_game") is None else r["motion_game"] for r in recs], float)
    stats = {}
    chg = np.array([np.nan if r.get("chg") is None else r["chg"] for r in recs], float)
    jumps = np.where(game & (np.nan_to_num(mot) >= P["teleport_motion"])
                     & (np.nan_to_num(match, nan=1.0) <= P["teleport_match"])
                     & (np.nan_to_num(chg, nan=1.0) >= P["teleport_chg"]))[0]
    chains: list[list[int]] = []
    for k in jumps:
        if chains and t[k] - t[chains[-1][-1]] <= P["teleport_gap"]:
            chains[-1].append(k)
        else:
            chains.append([k])
    for c in chains:
        big = float(np.nanmax(mot[c]))
        span = t[c[-1]] - t[c[0]]
        if len(c) == 1:
            add(c[0], "teleport", f"View jump in one frame (motion {big:.0f}, match {match[c[0]]:.2f}): teleport?",
                peak=round(big, 1))
        else:
            add(c[0], "teleport", f"{len(c)} view jumps in {span:.1f}s (teleport chain?)",
                end=round(float(t[c[-1]]), 2), peak=round(big, 1), count=len(c))
    steady = np.where(np.nan_to_num(match, nan=1.0) > P["teleport_match"], np.nan_to_num(shift), 0.0)
    sm = np.convolve(np.minimum(steady, 8.0), np.ones(5) / 5, mode="same")
    fast = game & (sm >= P["run_px"])
    i = 0
    while i < n:
        if not fast[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and (fast[j + 1] or (j + 1 < n and fast[min(n - 1, j + 1):min(n, j + 1 + FPS // 5)].any())):
            j += 1
        dur = t[j] - t[i] + 1 / FPS
        if dur >= P["run_min_dur"]:
            avg = float(np.mean(sm[i:j + 1]))
            add(i, "run_burst", f"Fast movement {dur:.1f}s, avg camera shift {avg:.1f} px/frame "
                f"(~{avg * FPS * 4:.0f} px/s at 720p; run/whirlwind/charge?)", end=round(float(t[j]), 2))
        i = j + 1
    if np.isfinite(shift).sum() > FPS * 5:
        stats["shift_median_px"] = round(float(np.nanmedian(shift)), 2)
        stats["shift_p99_px"] = round(float(np.nanpercentile(shift, 99)), 1)

    # --- text events: OCR lines not seen in the last ~5 s of that region
    recent: dict[str, list[tuple[float, str]]] = {}
    area_re = re.compile(r"^[A-Z][A-Za-z' ]{3,30}$")
    for i, r in enumerate(recs):
        for reg, txt in (r.get("ocr") or {}).items():
            hist = [(tt, s) for tt, s in recent.get(reg, []) if t[i] - tt <= 5]
            new = []
            for line in txt.split(" | "):
                key = _norm(line)
                if len(key) < 5 or any(key == s or (len(key) > 8 and (key in s or s in key)) for _, s in hist):
                    continue
                new.append(line)
                hist.append((t[i], key))
            recent[reg] = hist
            if not new:
                continue
            if reg == "top_right" and len(new) == 1 and area_re.match(new[0]):
                add(i, "area_change", f"Area name: {new[0]}")
            else:
                add(i, "text", f"Text ({reg}): " + " / ".join(new)[:200])

    ev.sort(key=lambda e: (e["t"], e["type"]))
    gl = life[game & ~np.isnan(life)]
    gm = mana[game & ~np.isnan(mana)]
    stats.update({
        "frames": n, "game_frames": int(game.sum()),
        "life_median": round(float(np.median(gl)), 1) if len(gl) else None,
        "mana_median": round(float(np.median(gm)), 1) if len(gm) else None,
        "life_min": round(float(gl.min()), 1) if len(gl) else None,
        "poison_frames": int(sum(1 for r in recs if r.get("poison"))),
        "ocr_samples_with_text": int(sum(1 for r in recs if r.get("ocr"))),
    })
    return ev, stats


# =============================================================================== the profile
class D2RProfile(Profile):
    name = "d2r"
    title = "Diablo II: Resurrected (PvP)"
    description = ("HUD calibration (life/mana orbs read against the on-screen numbers), life/mana %, "
                   "poison, death, potion refills, area changes, teleports / view jumps, fast movement. "
                   "Analysed at 25 fps (D2R's tick rate).")
    fps = 25.0
    generic_ocr = False   # this profile reads its own text regions (chat, centre messages, area name)
    generic_event_types = {"silence", "loud_peak", "static"}
    dense_types = {"life_drop", "death", "life_refill", "mana_refill", "poisoned", "area_change",
                   "off_hud", "teleport", "run_burst"}
    colors = {"life_drop": (40, 40, 255), "death": (0, 0, 200), "life_refill": (60, 200, 60),
              "mana_refill": (255, 140, 40), "poisoned": (60, 255, 60), "area_change": (0, 220, 255),
              "off_hud": (128, 128, 128), "teleport": (255, 0, 255), "run_burst": (255, 128, 192)}

    def detect(self, frames):
        """Two stable white glare spots at the orb positions plus red/blue liquid next to them."""
        if len(frames) < 4:
            return 0.0
        glare = find_glare(frames)
        if glare is None:
            return 0.0
        (gx1, gy1), (gx2, gy2), scale = glare
        H, W = frames[0].shape[:2]
        red = blue = 0.0
        for f in frames:
            hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
            r, g, b = liquid_masks(hsv)
            for key, gx, gy in (("life", gx1, gy1), ("mana", gx2, gy2)):
                x0, y0 = int(gx - GLARE_DX * scale), int(gy - GLARE_DY * scale)
                x1, y1 = int(x0 + ORB_W * scale), int(y0 + ORB_H * scale)
                x0, y0 = max(0, x0), max(0, y0)
                if key == "life":
                    red = max(red, float((r | g)[y0:y1, x0:x1].mean()))
                else:
                    blue = max(blue, float(b[y0:y1, x0:x1].mean()))
        return 0.9 if (blue > 0.2 and red > 0.05) else 0.3

    def calibrate(self, arc, cfg, *, force=False, sets=None, samples=None):
        if cfg is not None and not force and not sets:
            return Calibration(cfg, "existing")
        if not samples:
            raise SystemExit("could not decode any frames")
        status = "existing"
        fresh = cfg is None or force
        if fresh:
            cfg, notes = detect([f for _, f in samples])
            cfg["notes"] = notes
            status = "detected"
            log(f"calibration: {notes}")
            orb_numbers_check(arc, cfg, self.fps)
        changed = apply_sets(cfg, sets or [])
        if changed:
            status = "updated " + ", ".join(changed)
        cfg["frame_size"] = [arc.width, arc.height]
        # pick the sample where the HUD matches best and the orbs read something
        reader = HudReader(cfg, arc.width, arc.height)
        best, best_score = samples[len(samples) // 2], -1
        for t, f in samples:
            small = cv2.cvtColor(cv2.resize(f, (320, 180), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            life, mana, *_ = reader.read(f)
            score = reader.hud_score(small) + (0.3 if 5 < life <= 100 else 0) + (0.3 if 5 < mana <= 100 else 0)
            if score > best_score:
                best, best_score = (t, f), score
        t, f = best
        img = annotate(f, cfg, f"{arc.vid} @ {hms(t)}  HUD calibration ({status})")
        return Calibration(cfg, status, img, calibration_comment(arc.vid, cfg))

    def zoom_label(self, rec):
        return f"  L{rec['life']:.0f} M{rec['mana']:.0f}" if rec.get("life") is not None else ""

    zoom_legend = "L/M = life/mana % from frames.jsonl.gz when available."

    def analyzer(self, cfg, width, height, fps):
        return D2RAnalyzer(cfg, width, height, fps)

    def derive_events(self, recs, fps, cfg):
        ev, stats = derive(recs, fps)
        for e in ev:
            e["source"] = "d2r"
        return ev, stats


PROFILE = D2RProfile()
