"""Stage 4: HUD calibration.

  python -m ytdigest.calibrate <video_id> <workdir> [--archive-dir DIR] [--force]
         [--set life=x,y,w,h] [--set full_life=0.9] ...   (values in % of frame / raw fill)

Writes config/hud/<id>.json (unless it exists and --force/--set is not given) and
digests/<id>/calibration.png (annotated frame).
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from . import hud
from .common import digest_dir, hms, hud_config_path, log, save_json
from .video import Archive


def sample_frames(arc: Archive, n: int = 40):
    d = arc.duration
    lo, hi = (0.05 * d, 0.95 * d) if d > 60 else (0.0, max(0.1, d - 0.5))
    out = []
    for t in np.linspace(lo, hi, n):
        f = arc.frame_at(float(t))
        if f is not None:
            out.append((float(t), f))
    return out


def orb_numbers_check(arc: Archive, cfg: dict, n: int = 80) -> None:
    """Some D2R setups print 'Life: cur / max' above the orbs. If they are on screen, use them as
    ground truth: fit the raw-height -> value curve on half of the samples and report the error on
    the other half (held-out), then refit on all of them."""
    from . import ocr
    W, H = arc.width, arc.height
    reader = hud.HudReader({**cfg, "curve": None, "hud_ref": None}, W, H)
    d = arc.duration
    times = np.linspace(0.04 * d, 0.96 * d, n) if d > 60 else np.linspace(0, max(0.1, d - 0.5), n)
    rows = {"life": [], "mana": []}
    for t in times:
        f = arc.frame_at(float(t))
        if f is None:
            continue
        raw = {"life": reader.life.read(f)[0], "mana": reader.mana.read(f)[0]}
        for kind in rows:
            x0, y0, x1, y1 = hud.number_box(cfg[kind], W, H)
            got = ocr.read_orb_numbers(f[y0:y1, x0:x1])
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
        curve = hud.fit_curve(fit)
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
        curves[kind] = hud.fit_curve(pairs)
    if curves:
        cfg["curve"] = curves
    notes["orb_vs_on_screen_numbers"] = check
    log(f"orb check: {check}")


def apply_sets(cfg: dict, sets: list[str]) -> list[str]:
    changed = []
    for s in sets or []:
        k, _, v = s.partition("=")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("workdir", type=Path)
    ap.add_argument("--archive-dir", type=Path)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--set", action="append", default=[])
    a = ap.parse_args()

    cfg_path = hud_config_path(a.video_id)
    cfg = hud.load(a.video_id)
    a.workdir.mkdir(parents=True, exist_ok=True)
    if cfg is not None and not a.force and not a.set:
        (a.workdir / "calibration_status.txt").write_text("existing")
        log(f"using existing {cfg_path}")
        return
    arc = Archive(a.video_id, a.workdir, a.archive_dir)
    samples = sample_frames(arc, 40)
    if not samples:
        raise SystemExit("could not decode any frames")
    status = "existing"
    fresh = cfg is None or a.force
    if fresh:
        cfg, notes = hud.detect([f for _, f in samples])
        cfg["notes"] = notes
        status = "detected"
        log(f"calibration: {notes}")
    if fresh:
        orb_numbers_check(arc, cfg)
    changed = apply_sets(cfg, a.set)
    if changed:
        status = "updated " + ", ".join(changed)
    cfg["video_id"] = a.video_id
    cfg["frame_size"] = [arc.width, arc.height]
    save_json(cfg_path, cfg)

    # pick the sample where the HUD matches best and the orbs read something
    reader = hud.HudReader(cfg, arc.width, arc.height)
    best, best_score = samples[len(samples) // 2], -1
    for t, f in samples:
        small = cv2.cvtColor(cv2.resize(f, (320, 180), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        life, mana, *_ = reader.read(f)
        score = reader.hud_score(small) + (0.3 if 5 < life <= 100 else 0) + (0.3 if 5 < mana <= 100 else 0)
        if score > best_score:
            best, best_score = (t, f), score
    t, f = best
    img = hud.annotate(f, cfg, f"{a.video_id} @ {hms(t)}  HUD calibration ({status})")
    out = digest_dir(a.video_id) / "calibration.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img)
    (a.workdir / "calibration_status.txt").write_text(status)
    log(f"wrote {out} and {cfg_path}")


if __name__ == "__main__":
    main()
