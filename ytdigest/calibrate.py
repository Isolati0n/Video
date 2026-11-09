"""Stage 4: HUD calibration.

  python -m ytdigest.calibrate <video_id> <workdir> [--archive-dir DIR] [--force]
         [--set life=x,y,w,h] [--set full_life=0.9] ...   (values in % of frame / raw fill)

Writes config/hud/<id>.json (unless it exists and --force/--set is not given) and
digests/<id>/calibration.png (annotated frame).
"""
from __future__ import annotations

import argparse
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
    if cfg is None or a.force:
        cfg, notes = hud.detect([f for _, f in samples])
        cfg["notes"] = notes
        status = "detected"
        log(f"calibration: {notes}")
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
