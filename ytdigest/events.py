"""Stage 6: generic events from the stored frame data and OCR text (visual data only; audio is never read).

Generic event types (every profile gets these unless it narrows the list):
  scene_change   colour-histogram jump (a cut), rapid cut runs are merged
  motion_burst   large frame-to-frame change that is not a cut
  static         the picture hardly changes for a long stretch (slide, paused, idle)
  black          black / near-black frames (fade, loading screen)
  text_change    new on-screen text (whole-frame OCR, de-duplicated)

Profiles add their own events on top (see profiles/).
"""
from __future__ import annotations

import gzip
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from .common import hms

GP = {
    "cut_merge_s": 0.5, "rapid_cut_gap_s": 2.0, "rapid_cut_min": 4,
    "black_bright": 12.0, "black_min_s": 0.3,
    "motion_min_thr": 3.0, "motion_merge_s": 1.0, "motion_min_dur_s": 0.4, "motion_max_per_min": 6,
    "static_motion": 0.15, "static_min_s": 20.0,
    "text_recent_s": 12.0, "text_min_len": 8, "text_max_per_min": 8,
}


def load_frames(path: Path) -> list[dict]:
    recs = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            recs.append(json.loads(line))
    recs.sort(key=lambda r: r["f"])
    return recs


def load_text(paths: list[Path]) -> list[dict]:
    out = []
    for p in sorted(paths):
        with gzip.open(p, "rt") as fh:
            for line in fh:
                out.append(json.loads(line))
    out.sort(key=lambda r: r["f"])
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _similar(a: str, b: str) -> bool:
    """OCR misreads the same line differently from frame to frame ('Fine ENCHANTED' / 'Fike ENCHANTE')."""
    if len(a) < 6 or len(b) < 6 or abs(len(a) - len(b)) > 0.3 * max(len(a), len(b)):
        return False
    m = SequenceMatcher(None, a, b)
    return m.real_quick_ratio() >= 0.8 and m.quick_ratio() >= 0.8 and m.ratio() >= 0.8


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return []
    splits = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[idx[0], idx[splits + 1]]
    ends = np.r_[idx[splits], idx[-1]]
    return list(zip(starts.tolist(), ends.tolist()))


def dedupe_text(entries: list[dict]) -> list[dict]:
    """Whole-frame OCR repeats the same text every second. Returns entries whose 'new' lines were not
    seen in the previous few seconds: [{'t', 'f', 'new': [lines], 'all': [lines]}]."""
    recent: list[tuple[float, str]] = []
    out = []
    for e in entries:
        t = e["t"]
        recent = [(tt, k) for tt, k in recent if t - tt <= GP["text_recent_s"]]
        new, new_conf = [], []
        for ln in e["lines"]:
            key = _norm(ln["text"])
            if len(key) < 3:
                continue
            if any(key == k or (len(key) >= 8 and (key in k or k in key)) or _similar(key, k) for _, k in recent):
                continue
            new.append(ln["text"])
            new_conf.append(ln.get("conf", 100))
            recent.append((t, key))
        if new:
            out.append({"t": t, "f": e["f"], "new": new, "conf": new_conf, "all": [ln["text"] for ln in e["lines"]]})
    return out


def derive_generic(recs: list[dict], fps: float, text: list[dict]) -> tuple[list[dict], dict]:
    n = len(recs)
    if n == 0:
        return [], {}
    fpsi = max(1, int(round(fps)))
    t = np.array([r["t"] for r in recs])
    motion = np.array([np.nan if r.get("motion") is None else r["motion"] for r in recs], float)
    bright = np.array([r.get("bright", 128.0) for r in recs], float)
    cut = np.array([bool(r.get("cut")) for r in recs])
    ev: list[dict] = []

    def add(i, typ, desc, **kw):
        ev.append({"t": round(float(t[i]), 2), "f": recs[i]["f"], "type": typ, "desc": desc, "source": "generic", **kw})

    # --- black frames
    black = bright < GP["black_bright"]
    for a, b in _runs(black):
        dur = t[b] - t[a] + 1 / fps
        if dur >= GP["black_min_s"]:
            add(a, "black", f"Black / near-black picture for {dur:.1f}s (fade, loading or transition)", end=round(float(t[b]), 2))

    # --- scene changes (cuts), merged; runs of rapid cuts become one event
    cuts = [i for i in np.flatnonzero(cut) if not black[i]]
    groups: list[list[int]] = []
    for i in cuts:
        if groups and t[i] - t[groups[-1][-1]] <= GP["cut_merge_s"]:
            groups[-1].append(i)
        else:
            groups.append([i])
    clusters: list[list[list[int]]] = []
    for g in groups:
        if clusters and t[g[0]] - t[clusters[-1][-1][-1]] <= GP["rapid_cut_gap_s"]:
            clusters[-1].append(g)
        else:
            clusters.append([g])
    cut_idx = set()
    for c in clusters:
        first = c[0][0]
        if len(c) >= GP["rapid_cut_min"]:
            span = t[c[-1][-1]] - t[first]
            add(first, "scene_change", f"Rapid cuts: {len(c)} scene changes in {span:.1f}s", end=round(float(t[c[-1][-1]]), 2), count=len(c))
        else:
            for g in c:
                add(g[0], "scene_change", "Scene change (picture jumps to different content)")
        for g in c:
            cut_idx.update(g)

    # --- motion bursts: large change that is not a cut or black
    ok = ~np.isnan(motion)
    stats: dict = {}
    if ok.sum() > fpsi * 5:
        base = float(np.nanmedian(motion))
        thr = max(4 * base, float(np.nanpercentile(motion, 98)), GP["motion_min_thr"])
        mm = motion.copy()
        for i in cut_idx:
            mm[max(0, i - 1):i + 2] = np.nan
        mm[black] = np.nan
        hot = np.nan_to_num(mm) > thr
        bursts = []
        for a, b in _runs(hot):
            if bursts and t[a] - t[bursts[-1][1]] <= GP["motion_merge_s"]:
                bursts[-1][1] = b
                bursts[-1][2] += int(hot[a:b + 1].sum())
            else:
                bursts.append([a, b, int(hot[a:b + 1].sum())])
        cand = []
        for a, b, nh in bursts:
            dur = t[b] - t[a] + 1 / fps
            if dur >= GP["motion_min_dur_s"] or nh >= 3:
                peak = float(np.nanmax(mm[a:b + 1]))
                cand.append((peak * max(dur, 0.5), a, b, nh, dur, peak))
        cap = int(GP["motion_max_per_min"] * max(1.0, t[-1] / 60))
        if len(cand) > cap:  # busy footage (gameplay): keep the strongest ones
            cand = sorted(cand, reverse=True)[:cap]
        for _, a, b, nh, dur, peak in sorted(cand, key=lambda x: x[1]):
            add(a, "motion_burst", f"Large motion for {dur:.1f}s (peak {peak:.0f}, {nh} hot frames; threshold {thr:.1f})",
                end=round(float(t[b]), 2), peak=round(peak, 1))
        stats["motion_median"] = round(base, 2)
        stats["motion_threshold"] = round(thr, 2)

    deduped = dedupe_text(text)
    # --- long static stretches
    if ok.sum() > fpsi * 5:
        k = fpsi
        sm = np.convolve(np.nan_to_num(motion, nan=0.0), np.ones(k) / k, mode="same")
        static = (sm < GP["static_motion"]) & ~black & ok
        static_s = 0.0
        # A slide swap or a few changed lines barely move the mean pixel difference, so a stretch is
        # also broken wherever new on-screen text appears or the scene cuts.
        breaks = np.array(sorted([d["t"] for d in deduped if any(len(x) >= GP["text_min_len"] for x in d["new"])]
                                 + [float(t[i]) for i in cut_idx]))
        for a, b in _runs(static):
            cuts_in = breaks[(breaks > t[a] + 1e-6) & (breaks < t[b])] if len(breaks) else breaks
            edges = [a] + [int(np.searchsorted(t, x)) for x in cuts_in] + [b + 1]
            for lo, hi in zip(edges, edges[1:]):
                if hi - 1 <= lo:
                    continue
                dur = t[hi - 1] - t[lo] + 1 / fps
                if dur >= GP["static_min_s"]:
                    add(lo, "static", f"Picture static for {dur:.0f}s (slide, paused video, idle screen)", end=round(float(t[hi - 1]), 2))
                    static_s += dur
        stats["static_seconds"] = round(static_s, 1)

    # --- text changes from whole-frame OCR
    per_min: dict[int, int] = {}
    shown = 0
    for d in deduped:
        subst = [x for x in d["new"] if len(x) >= GP["text_min_len"]]
        if not subst:
            continue
        minute = int(d["t"] // 60)
        if per_min.get(minute, 0) >= GP["text_max_per_min"]:
            continue
        per_min[minute] = per_min.get(minute, 0) + 1
        i = int(np.argmin(np.abs(t - d["t"])))
        add(i, "text_change", "New on-screen text: " + " / ".join(subst)[:220])
        shown += 1
    stats.update({"ocr_runs_with_text": len(text), "ocr_new_text_entries": len(deduped),
                  "ocr_lines_unique": len({_norm(x) for d in deduped for x in d["new"]})})

    ev.sort(key=lambda e: (e["t"], e["type"]))
    stats.update({"frames": n, "cuts": int(cut.sum()), "black_frames": int(black.sum())})
    return ev, stats


def write_md(ev: list[dict], stats: dict, path: Path, title: str, fps: float, profile: str, note: str = "") -> dict:
    counts: dict[str, int] = {}
    for e in ev:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    lines = [f"# Events: {title}", "",
             f"Derived by code from `frames.jsonl.gz` ({fps:g} fps, profile `{profile}`), the whole-frame OCR "
             "(visual data only; the audio track is not used). '?' marks guesses about the cause. " + note, "",
             "| type | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(counts.items())]
    lines += ["", "Stats: " + ", ".join(f"{k}={v}" for k, v in stats.items()), "", "## Timeline", ""]
    for e in ev:
        src = "" if e.get("source") == "generic" else f" [{e.get('source')}]"
        lines.append(f"- [{hms(e['t'])}] (f{e['f']}) **{e['type']}**{src}: {e['desc']}")
    path.write_text("\n".join(lines) + "\n")
    return counts
