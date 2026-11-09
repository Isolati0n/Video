"""Stage 6: derive events from frames.jsonl.gz.

Event types: life_drop, life_refill, mana_refill, poisoned, death, area_change, off_hud, teleport,
run_burst, text.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import numpy as np

from .common import FPS, hms

P = {  # thresholds (life/mana in % points; shift in px at 320 wide)
    "drop_pts": 10, "drop_window": 1.0,        # tuned on real footage: biggest 1 s fall in a 5 min PvP clip was 19 pts
    "refill_pts": 12, "refill_window": 0.6,
    "death_life": 3, "death_hold": 0.4,
    "teleport_motion": 5.0, "teleport_match": 0.2, "teleport_chg": 0.6, "teleport_gap": 1.5,  # new view in one frame; chains merge within the gap
    "run_px": 1.2, "run_min_dur": 0.8,         # sustained camera shift per frame (walking is ~0.2-0.5 here)
}


def load_frames(path: Path):
    recs = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            recs.append(json.loads(line))
    recs.sort(key=lambda r: r["f"])
    return recs


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


def derive(recs: list[dict]) -> tuple[list[dict], dict]:
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
    mot = np.array([np.nan if r.get("motion") is None else r["motion"] for r in recs], float)
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


def write_md(ev: list[dict], stats: dict, path: Path, title: str):
    counts: dict[str, int] = {}
    for e in ev:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    lines = [f"# Events: {title}", "",
             "Derived by code from `frames.jsonl.gz` (25 fps). Orb values are estimates from orb fill; "
             "'?' marks guesses about the cause.", "",
             "| type | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(counts.items())]
    lines += ["", "Stats: " + ", ".join(f"{k}={v}" for k, v in stats.items()), "", "## Timeline", ""]
    for e in ev:
        lines.append(f"- [{hms(e['t'])}] (f{e['f']}) **{e['type']}**: {e['desc']}")
    path.write_text("\n".join(lines) + "\n")
    return counts
