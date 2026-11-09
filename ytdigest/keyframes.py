"""Choose which frames go on the contact sheets.

Regular keyframes: walk the thumbnail candidates in time order and keep one when
  - a scene change happened since the last kept frame            (reason "scene"),
  - the picture changed (perceptual hash distance >= DEDUPE_BITS) (reason "changed"),
  - or MAX_GAP seconds passed with nothing kept                   (reason "heartbeat"),
never closer than MIN_GAP seconds apart. Near-identical frames are skipped (the idea of grouping frames
by perceptual hash comes from usedeepmark/watch-for-me), so a slide that stays up for ten minutes is
one tile, and busy gameplay is limited to one tile every MIN_GAP seconds.

Dense frames: a few consecutive frames at the analysis rate around important events. When there are
too many events, the budget is shared out so rare event types are never crowded out by frequent ones.
"""
from __future__ import annotations

import os

DEDUPE_BITS = int(os.environ.get("KEYFRAME_DEDUPE_BITS", "6"))
MIN_GAP = float(os.environ.get("KEYFRAME_MIN_GAP", "2"))
MAX_GAP = float(os.environ.get("KEYFRAME_MAX_GAP", "30"))
DENSE_MAX_PER_HOUR = int(os.environ.get("DENSE_EVENTS_PER_HOUR", "240"))


def select(cands: list[dict], phash_by_f: dict[int, str], cut_times: list[float]) -> list[dict]:
    """cands: [{'t', 'f', 'path'}] sorted by t. Returns the kept ones with a 'reason'."""
    kept: list[dict] = []
    last_t, last_h = -1e9, None
    ci = 0
    for c in cands:
        h = int(phash_by_f[c["f"]], 16) if c["f"] in phash_by_f else None
        while ci < len(cut_times) and cut_times[ci] <= c["t"]:
            ci += 1
        cut_since = any(last_t < ct <= c["t"] for ct in cut_times[max(0, ci - 8):ci])
        reason = None
        if last_h is None:
            reason = "first"
        elif c["t"] - last_t >= MAX_GAP:
            reason = "heartbeat"
        elif c["t"] - last_t >= MIN_GAP:
            if cut_since:
                reason = "scene"
            elif h is not None and bin(h ^ last_h).count("1") >= DEDUPE_BITS:
                reason = "changed"
        if reason:
            kept.append({**c, "reason": reason})
            last_t, last_h = c["t"], h
    return kept


def dense_events(events: list[dict], dense_types: set[str], duration_s: float) -> list[dict]:
    """The events that get dense frames, within DENSE_MAX_PER_HOUR (water-filling over event types)."""
    cap = max(20, int(DENSE_MAX_PER_HOUR * max(1.0, duration_s / 3600)))
    by_type: dict[str, list[dict]] = {}
    for e in events:
        if e["type"] in dense_types:
            by_type.setdefault(e["type"], []).append(e)
    total = sum(len(v) for v in by_type.values())
    if total <= cap:
        return sorted((e for v in by_type.values() for e in v), key=lambda e: e["t"])
    out: list[dict] = []
    remaining = cap
    types = sorted(by_type, key=lambda k: len(by_type[k]))
    for i, k in enumerate(types):
        share = remaining // (len(types) - i)
        items = by_type[k]
        if len(items) > share:
            step = len(items) / share
            items = [items[int(j * step)] for j in range(share)]
        out.extend(items)
        remaining -= len(items)
    return sorted(out, key=lambda e: e["t"])
