"""TEMPLATE for a new profile. The leading underscore keeps it out of the registry; copy it to
`profiles/<yourname>.py` (no underscore) to activate it. See profiles/README.md.

Example target: Super Smash Bros. Melee (60 fps): damage percent counters, stocks, stage.
Nothing here is implemented yet; it only shows where each piece goes.
"""
from __future__ import annotations

import numpy as np

from . import Analyzer, Calibration, Profile


class MeleeAnalyzer(Analyzer):
    """Runs once per frame while the video streams by (60 fps for Melee). Add keys to `rec`;
    they are stored in frames.jsonl.gz next to the generic fields. Keep it cheap: this runs on
    every frame. Expensive things (OCR) go through rec["_ocr_crops"] and run in a thread pool,
    once per second at most."""

    def __init__(self, cfg: dict, width: int, height: int, fps: float):
        self.cfg = cfg
        # TODO: turn the fractional regions from cfg (x, y, w, h in 0..1) into pixel boxes, e.g.
        #   self.p1_percent = box(cfg["p1_percent"], width, height)
        self.fps = int(round(fps))
        # self.ocr_fn = a function {region: crop} -> {region: text} (see ytdigest/ocr.py)

    def process(self, frame: np.ndarray, gray: np.ndarray, k: int, t: float, rec: dict) -> None:
        # TODO: read the percent counters / stock icons from `frame` and store simple numbers:
        #   rec["p1_pct"] = 87        rec["p1_stocks"] = 3
        # To OCR a region once per second:
        #   if k % self.fps == 0: rec["_ocr_crops"] = {"p1_percent": frame[y0:y1, x0:x1].copy()}
        pass


class MeleeProfile(Profile):
    name = "melee"
    title = "Super Smash Bros. Melee"
    description = "Percent counters, stocks, stage, kills (template; not implemented)."
    fps = None                      # None = the video's native rate (60 for Melee), capped at 60
    generic_ocr = True              # keep whole-frame OCR (names, tags, damage text)
    generic_event_types = None      # keep all generic events (scene changes, motion bursts, ...)
    dense_types = {"stock_lost", "percent_spike"}      # events that get dense frames on the contact sheets
    colors = {"stock_lost": (0, 0, 220), "percent_spike": (0, 165, 255)}

    def suggest(self, frames):
        # TODO: return 0..1 when the frames look like Melee (e.g. HUD layout at the bottom). Return 0.0
        # when unsure: a wrong suggestion is worse than none, and a profile is never chosen silently.
        return 0.0

    def calibrate(self, arc, cfg, *, force=False, sets=None, samples=None):
        # Optional. `samples` is a list of (time, frame). Find the regions once per video, return them as
        # `cfg` (stored in config/videos/<id>.json) plus an annotated image for the issue comment.
        # With defaults() you can start from config/profiles/melee.json:
        #   cfg = cfg or self.defaults()
        return Calibration(cfg or self.defaults(), status="existing" if cfg else "detected",
                           comment_md="Regions are taken from config/profiles/melee.json. "
                                      "Override with `/hud <id> p1_percent=x,y,w,h` (percent of the frame).")

    def analyzer(self, cfg, width, height, fps):
        return MeleeAnalyzer(cfg, width, height, fps)

    def derive_events(self, recs, fps, cfg):
        # recs: every frame record, sorted by frame number. Return (events, stats).
        # An event is a dict: {"t": seconds, "f": frame, "type": "stock_lost", "desc": "P1 lost a stock", "source": "melee"}
        # Optional keys: "end" (seconds) for a stretch. Keep `desc` short and factual.
        events: list[dict] = []
        # TODO: e.g. a stock is lost when p1_stocks drops by one between two readable frames
        return events, {}


# PROFILE = MeleeProfile()   # <- uncomment in the copied module to register it
