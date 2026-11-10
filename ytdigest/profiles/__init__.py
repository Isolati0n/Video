"""Profiles: optional, game/content-specific plug-ins on top of the generic core.

The core analyses EVERY video the same way (native fps, motion, cuts, hashes, whole-frame OCR,
generic events). A profile adds three optional things:

  1. calibration     find regions on screen once per video (e.g. where a health bar is)
  2. frame analyzer  extra per-frame fields (e.g. a health percentage), computed while the frames stream by
  3. event rules     extra events derived from the stored frames

Every profile also exposes a `detect(frames)` detector: when a video is digested with the generic profile,
all registered detectors look at a few sample frames and any that recognise the content are suggested in the
issue ("this looks like ..."). A profile is never selected silently.

See profiles/README.md for how to add one. A module in this folder is picked up automatically when
its name does not start with an underscore and it defines `PROFILE`.
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass

import numpy as np

from ..common import fps_cap


@dataclass
class Calibration:
    cfg: dict                      # profile-specific per-video config, stored in config/videos/<id>.json
    status: str = "detected"       # detected | existing | updated ...
    image: np.ndarray | None = None  # annotated frame shown in the issue
    comment_md: str = ""           # text posted under the image


class Analyzer:
    """Per-frame extras. `process` may add keys to `rec` (the frame record being written).
    To request OCR of regions, put {region: crop} under rec["_ocr_crops"]; the result is stored
    under rec["ocr"] and `ocr_fn(crops) -> {region: text}` is used to read it."""
    ocr_fn = None

    def process(self, frame: np.ndarray, gray: np.ndarray, k: int, t: float, rec: dict) -> None:
        pass


class Profile:
    name = "generic"
    title = "Generic video"
    description = "Any video: talks, tutorials, screen recordings, games."
    fps: float | None = None          # None = the video's native fps (capped at MAX_FPS)
    generic_ocr = True                # whole-frame OCR; a profile with its own regions can turn it off
    generic_event_types: set[str] | None = None   # None = keep every generic event type
    dense_types: set[str] = {"scene_change", "motion_burst", "black"}   # events that get dense frames
    colors: dict[str, tuple] = {}     # event type -> BGR border colour on contact sheets

    # -- optional hooks -------------------------------------------------------------------------
    def detect(self, frames: list[np.ndarray]) -> float:
        """The profile's detector: 0..1, how much do these sample frames look like this profile's content?
        Used only to SUGGEST a profile in the issue. Return 0.0 when unsure: a wrong suggestion is
        worse than none."""
        return 0.0

    def calibrate(self, arc, cfg: dict | None, *, force: bool = False, sets: list[str] | None = None,
                  samples: list[tuple[float, np.ndarray]] | None = None) -> Calibration | None:
        return None

    def analyzer(self, cfg: dict, width: int, height: int, fps: float) -> Analyzer | None:
        return None

    def derive_events(self, recs: list[dict], fps: float, cfg: dict) -> tuple[list[dict], dict]:
        return [], {}

    def zoom_label(self, rec: dict) -> str:
        """Short text added to each frame label in /zoom sheets, from the stored per-frame record."""
        return ""

    zoom_legend = ""   # one sentence explaining zoom_label, shown in the zoom index

    def events_note(self) -> str:
        return ""

    def defaults(self) -> dict:
        """Optional per-profile defaults from config/profiles/<name>.json (regions, thresholds, ...)."""
        from ..common import load_json, profile_defaults_path
        return load_json(profile_defaults_path(self.name), {}) or {}


def analysis_fps(native_fps: float, profile: Profile) -> float:
    """The rate frames are analysed at: the profile's own if it sets one, else native, capped."""
    return float(profile.fps) if profile.fps else min(float(native_fps), fps_cap())


_REGISTRY: dict[str, Profile] = {}


def _load_all():
    if _REGISTRY:
        return
    for m in pkgutil.iter_modules(__path__):  # type: ignore[name-defined]
        if m.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{m.name}")
        prof = getattr(mod, "PROFILE", None)
        if isinstance(prof, Profile):
            _REGISTRY[prof.name] = prof


def names() -> list[str]:
    _load_all()
    return sorted(_REGISTRY)


def get(name: str | None) -> Profile:
    _load_all()
    name = (name or "generic").strip().lower()
    if name not in _REGISTRY:
        raise SystemExit(f"unknown profile {name!r}; available: {', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[name]


def suggestions(frames: list[np.ndarray], exclude: str = "generic") -> list[tuple[str, float]]:
    _load_all()
    out = []
    for n, p in _REGISTRY.items():
        if n == exclude:
            continue
        try:
            out.append((n, float(p.detect(frames))))
        except Exception as e:  # a broken detector must not break a digest
            print(f"detector of profile {n} failed: {e}", file=__import__("sys").stderr)
    return sorted([(n, c) for n, c in out if c >= 0.5], key=lambda x: -x[1])
