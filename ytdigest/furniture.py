"""Suppress screen furniture in OCR output: menu bars, taskbars, watermarks, persistent overlays.

A line is furniture when the same text keeps appearing at (about) the same screen position through
the video: it is present in at least MIN_SHARE of the OCR'd frames AND in at least 4 of the 5 equal time
slices (so a slide that merely stays up for half the video is NOT furniture). Text at a position that
keeps changing (a clock, a score, damage numbers, subtitles) forms many small groups, none of which
is persistent, so it is kept. The suppressed lines are returned so nothing is hidden silently.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

MIN_SHARE = 0.5
MIN_SLICES = 4      # of 5 equal time slices
CELL_X, CELL_Y = 80, 40   # permille cell size: 12 columns x 25 rows


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _same(a: str, b: str) -> bool:
    """Same line, allowing OCR misreads of LETTERS. Different digits = different text (a clock, a page
    number, a score): that is content that changes, never furniture."""
    if a == b:
        return True
    if _digits(a) != _digits(b):
        return False
    if len(a) < 4 or len(b) < 4:
        return False
    if len(a) >= 8 and (a in b or b in a):
        return True
    if abs(len(a) - len(b)) > 0.3 * max(len(a), len(b)):
        return False
    m = SequenceMatcher(None, a, b)
    return m.real_quick_ratio() >= 0.78 and m.ratio() >= 0.78


def suppress(entries: list[dict], duration: float) -> tuple[list[dict], list[dict]]:
    """entries: [{'t','f','lines':[{'text','conf','x','y'}]}] -> (entries without furniture, furniture list)."""
    n = len(entries)
    if n < 20 or duration <= 0:
        return entries, []
    clusters: list[dict] = []   # {"cx","cy","key","text","frames":set,"slices":set,"members":int}
    by_cell: dict[tuple[int, int], list[dict]] = {}
    assign: dict[tuple[int, int], dict] = {}   # (entry idx, line idx) -> cluster
    for ei, e in enumerate(entries):
        sl = min(4, int(5 * e["t"] / duration))
        for li, ln in enumerate(e["lines"]):
            if "x" not in ln:
                continue
            key = _norm(ln["text"])
            if len(key) < 3:
                continue
            cx, cy = ln["x"] // CELL_X, ln["y"] // CELL_Y
            found = None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for c in by_cell.get((cx + dx, cy + dy), ()):
                        if _same(key, c["key"]):
                            found = c
                            break
                    if found:
                        break
                if found:
                    break
            if found is None:
                found = {"cx": cx, "cy": cy, "key": key, "text": ln["text"], "frames": set(), "slices": set()}
                clusters.append(found)
                by_cell.setdefault((cx, cy), []).append(found)
            found["frames"].add(ei)
            found["slices"].add(sl)
            assign[(ei, li)] = found
    furn = [c for c in clusters if len(c["frames"]) / n >= MIN_SHARE and len(c["slices"]) >= MIN_SLICES]
    ids = {id(c) for c in furn}
    kept = []
    for ei, e in enumerate(entries):
        lines = [ln for li, ln in enumerate(e["lines"]) if id(assign.get((ei, li))) not in ids]
        if lines:
            kept.append({**e, "lines": lines})
    report = sorted(({"text": c["text"], "share": round(len(c["frames"]) / n, 2), "x": c["cx"] * CELL_X, "y": c["cy"] * CELL_Y}
                     for c in furn), key=lambda r: -r["share"])
    return kept, report
