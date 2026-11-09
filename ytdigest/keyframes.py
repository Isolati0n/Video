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

import bisect
import os
import re
from difflib import SequenceMatcher

DEDUPE_BITS = int(os.environ.get("KEYFRAME_DEDUPE_BITS", "6"))
MIN_GAP = float(os.environ.get("KEYFRAME_MIN_GAP", "2"))
MAX_GAP = float(os.environ.get("KEYFRAME_MAX_GAP", "30"))
DENSE_MAX_PER_HOUR = int(os.environ.get("DENSE_EVENTS_PER_HOUR", "240"))
TEXT_CHANGE = 0.5          # a new keyframe when at least this share of the on-screen lines is new
TEXT_MIN_GAP = 3.0         # seconds between text-driven keyframes
TITLE_MIN_Y = 60           # permille of the frame height: lines above this are the menu-bar / status strip
SAME_TEXT_RELAXED = 0.6
TEXT_MAX_PER_HOUR = 400
SAME_TEXT = 0.8            # text overlap at or above this = the same slide (see select)
BIG_PICTURE_CHANGE = 16    # bits of hash distance that still count as a new picture when the text is the same
SLIDE_LIKE_MOTION = 0.5    # median motion below this = a slide / talk / document style video


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _match(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) < 5 or len(b) < 5 or abs(len(a) - len(b)) > 0.3 * max(len(a), len(b)):
        return False
    if len(a) >= 8 and (a in b or b in a):
        return True
    m = SequenceMatcher(None, a, b)
    return m.real_quick_ratio() >= 0.78 and m.ratio() >= 0.78


CAPTION_MIN_Y = 750        # permille of the frame height: the bottom strip where subtitles / captions are burned in


def text_keys(lines: list[dict], skip_captions: bool = False) -> list[str]:
    """Normalised line keys. skip_captions drops the bottom strip: burned-in subtitles change every few seconds
    without the picture changing, so they must not drive keyframes (they stay in ocr.md)."""
    return [k for k in (_key(x["text"]) for x in lines if not (skip_captions and x.get("y", 0) >= CAPTION_MIN_Y))
            if len(k) >= 4]


def overlap(a: list[str], b: list[str]) -> float:
    """Share of the lines in `a` that also appear (fuzzily) in `b`. 1.0 = the same text."""
    if not a:
        return 1.0 if not b else 0.0
    return sum(1 for x in a if any(_match(x, y) for y in b)) / len(a)


class TextIndex:
    """Nearest-in-time OCR entry (content lines only) for a keyframe time."""

    def __init__(self, entries: list[dict]):
        self.entries = sorted(entries, key=lambda e: e["t"])
        self.ts = [e["t"] for e in self.entries]

    def at(self, t: float, max_dt: float = 1.6) -> dict | None:
        i = bisect.bisect_left(self.ts, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(self.ts) and abs(self.ts[j] - t) <= max_dt and (best is None or abs(self.ts[j] - t) < abs(self.ts[best] - t)):
                best = j
        return None if best is None else self.entries[best]


def text_keyframes(entries: list[dict], thumb_frames: set[int], duration_s: float) -> list[dict]:
    """A keyframe whenever the on-screen text changes substantially. Histogram cuts miss slide changes
    on same-coloured slides; this does not. Only entries that have a thumbnail can become keyframes."""
    out, last, last_top, last_t = [], None, None, -1e9
    pending_top = None          # a new title must be seen in two consecutive readings before it counts
    for e in sorted(entries, key=lambda e: e["t"]):
        keys = text_keys(e["lines"], skip_captions=True)
        if len(keys) < 2:
            continue
        # The title = the top-most CONFIDENT line below the very top strip (menu bars, window chrome and
        # status bars live there and flicker). A different title is a different slide even when other
        # lines happen to match.
        tops = [(x.get("y", 0), _key(x["text"])) for x in e["lines"]
                if TITLE_MIN_Y <= x.get("y", 0) < CAPTION_MIN_Y and x.get("conf", 100) >= 75 and len(_key(x["text"])) >= 6]
        top = min(tops)[1] if tops else None
        title_changed = False
        if top is not None and last_top is not None and not _match(top, last_top):
            if pending_top is not None and _match(top, pending_top):
                title_changed = True
            else:
                pending_top = top
        else:
            pending_top = None
        changed = last is None or (1.0 - overlap(keys, last)) >= TEXT_CHANGE or title_changed
        if changed and e["f"] in thumb_frames and e["t"] - last_t >= TEXT_MIN_GAP:
            out.append({"t": e["t"], "f": e["f"], "reason": "text"})
            last, last_top, last_t, pending_top = keys, top, e["t"], None
        elif changed and last is None:
            last, last_top = keys, top
    cap = max(20, int(TEXT_MAX_PER_HOUR * max(1.0, duration_s / 3600)))
    if len(out) > cap:
        out = [out[int(i * len(out) / cap)] for i in range(cap)]
    return out


def select(cands: list[dict], phash_by_f: dict[int, str], cut_times: list[float],
           text_index: "TextIndex | None" = None, text_kf: list[dict] | None = None, slide_like: bool = False) -> list[dict]:
    """cands: [{'t', 'f', 'path'}] sorted by t. Returns the kept ones with a 'reason'.
    In a slide-like video a small picture change (a pointer, an animation, a presenter or host moving) does not
    make a keyframe unless the on-screen text differs; a big one (BIG_PICTURE_CHANGE bits) always does."""
    kept: list[dict] = []
    last_t, last_h, last_keys = -1e9, None, None
    tf = {t["f"] for t in (text_kf or [])}
    ci = 0
    for c in cands:
        h = int(phash_by_f[c["f"]], 16) if c["f"] in phash_by_f else None
        while ci < len(cut_times) and cut_times[ci] <= c["t"]:
            ci += 1
        cut_since = any(last_t < ct <= c["t"] for ct in cut_times[max(0, ci - 8):ci])
        reason = None
        if c["f"] in tf:                       # on-screen text changed substantially: one sequential walk, so
            reason = "text"                    # later picture-driven decisions see this keyframe
        elif last_h is None:
            reason = "first"
        elif c["t"] - last_t >= MAX_GAP:
            reason = "heartbeat"
        elif c["t"] - last_t >= MIN_GAP:
            if cut_since:
                reason = "scene"
            elif h is not None:
                dist = bin(h ^ last_h).count("1")
                if dist >= DEDUPE_BITS:
                    reason = "changed"
                    if slide_like and dist < BIG_PICTURE_CHANGE:
                        # a small picture change (a pointer, an animation, a presenter or host moving) is only a
                        # new keyframe when the text proves the content changed
                        e = text_index.at(c["t"]) if text_index is not None else None
                        keys = text_keys(e["lines"], True) if e is not None else []
                        text_differs = len(keys) >= 2 and bool(last_keys) and overlap(keys, last_keys) < SAME_TEXT
                        if not text_differs:
                            reason = None
        if reason:
            kept.append({**c, "reason": reason})
            last_t, last_h = c["t"], h
            e = text_index.at(c["t"]) if text_index else None
            last_keys = text_keys(e["lines"], True) if e else None
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
