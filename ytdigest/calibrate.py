"""Stage 4: pick the profile, run its calibration (if it has one), suggest a profile (never switch silently).

  python -m ytdigest.calibrate <video_id> <workdir> [--archive-dir DIR] [--profile NAME]
         [--force] [--set key=value ...]

Writes config/videos/<id>.json (profile + that profile's calibration), and, when the profile
calibrates something, digests/<id>/calibration.png and <workdir>/calibration_comment.md.
Without --profile the profile saved for this video is kept; a video that has none uses "generic".
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from . import profiles, videoconfig
from .common import digest_dir, gh_output, log
from .video import Archive


def sample_frames(arc: Archive, n: int, fps: float | None = None) -> list[tuple[float, np.ndarray]]:
    d = arc.duration
    lo, hi = (0.05 * d, 0.95 * d) if d > 60 else (0.0, max(0.1, d - 0.5))
    out = []
    for t in np.linspace(lo, hi, n):
        f = arc.frame_at(float(t), fps=fps)
        if f is not None:
            out.append((float(t), f))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("workdir", type=Path)
    ap.add_argument("--archive-dir", type=Path)
    ap.add_argument("--profile", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--set", action="append", default=[])
    a = ap.parse_args()
    a.workdir.mkdir(parents=True, exist_ok=True)

    saved = videoconfig.load(a.video_id) or {}
    wanted = (a.profile or "").strip().lower()
    name = wanted or saved.get("profile") or "generic"
    prof = profiles.get(name)
    switched = bool(wanted) and wanted != saved.get("profile", "generic")
    cfg = None if switched else (saved.get("config") or None)
    force = a.force or switched
    if switched:
        log(f"profile for {a.video_id}: {saved.get('profile', 'generic')} -> {name}; calibrating from scratch")

    arc = Archive(a.video_id, a.workdir, a.archive_dir)
    fps = profiles.analysis_fps(arc.native_fps, prof)
    status, image, comment = "none", None, ""

    if type(prof).calibrate is not profiles.Profile.calibrate:  # the profile has a calibration step
        if cfg is not None and not force and not a.set:
            res = prof.calibrate(arc, cfg, force=False, sets=[])
        else:
            res = prof.calibrate(arc, cfg, force=force, sets=a.set, samples=sample_frames(arc, 40, fps))
        if res is not None:
            cfg, status, image, comment = res.cfg, res.status, res.image, res.comment_md
    videoconfig.save(a.video_id, name, cfg)

    out = digest_dir(a.video_id)
    out.mkdir(parents=True, exist_ok=True)
    if image is not None:
        cv2.imwrite(str(out / "calibration.png"), image)
    elif status == "none":
        (out / "calibration.png").unlink(missing_ok=True)   # left over from a run with another profile
    (a.workdir / "calibration_status.txt").write_text(status)
    (a.workdir / "calibration_comment.md").write_text(comment)

    # never switch silently: a generic run may only SUGGEST a profile
    suggestion = ""
    if name == "generic" and not saved:
        found = profiles.suggestions([f for _, f in sample_frames(arc, 12)])
        if found:
            best, conf = found[0]
            p = profiles.get(best)
            suggestion = (f"💡 This video looks like **{p.title}** (confidence {conf:.0%}). It is being digested with "
                          f"the generic profile. To use the `{best}` profile ({p.description}) comment "
                          f"`/digest profile: {best}`.")
    (a.workdir / "suggestion.md").write_text(suggestion)
    log(f"profile={name} fps={fps:g} status={status} suggestion={'yes' if suggestion else 'no'}")
    gh_output(profile=name, analysis_fps=f"{fps:g}", status=status)


if __name__ == "__main__":
    main()
