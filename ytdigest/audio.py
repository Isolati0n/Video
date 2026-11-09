"""Cheap audio events from the archived video's audio track: silence and loudness peaks.

Decodes mono 8 kHz with ffmpeg, measures RMS in 0.5 s windows (dBFS). Silence = quieter than
-50 dBFS for >= 8 s. A loud peak = a 1 s level >= 12 dB above the typical (median) level and louder
than -25 dBFS; nearby peaks merge. It does not understand the audio: it cannot tell speech from
music or an explosion from a cheer.
"""
from __future__ import annotations

import subprocess

import numpy as np

from .video import Archive

WIN = 0.5
SR = 8000
SILENCE_DB, SILENCE_MIN_S = -50.0, 8.0
PEAK_OVER_DB, PEAK_MIN_DB, PEAK_MERGE_S, PEAK_MAX_PER_HOUR = 12.0, -25.0, 3.0, 60


def levels(arc: Archive) -> np.ndarray | None:
    """dBFS per 0.5 s window across the whole video (None when there is no audio track)."""
    out = np.zeros(0)
    for path, ps, pe, _ in arc.parts_for(0, arc.duration):
        raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path), "-vn",
                              "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True).stdout
        if not raw:
            continue
        x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
        w = int(SR * WIN)
        n = len(x) // w
        if n == 0:
            continue
        rms = np.sqrt((x[:n * w].reshape(n, w) ** 2).mean(1) + 1e-12)
        db = 20 * np.log10(rms)
        start_idx = int(round(ps / WIN))
        if len(out) < start_idx:
            out = np.r_[out, np.full(start_idx - len(out), -90.0)]
        out = np.r_[out[:start_idx], db]
    return out if len(out) else None


def audio_events(arc: Archive, fps: float) -> tuple[list[dict], dict]:
    db = levels(arc)
    if db is None:
        return [], {"audio": "none"}
    ev: list[dict] = []

    def add(t, typ, desc, **kw):
        ev.append({"t": round(float(t), 2), "f": int(round(t * fps)), "type": typ, "desc": desc, "source": "generic", **kw})

    stats = {"audio_median_db": round(float(np.median(db)), 1), "audio_max_db": round(float(db.max()), 1)}
    quiet = db < SILENCE_DB
    idx = np.flatnonzero(quiet)
    if len(idx):
        splits = np.flatnonzero(np.diff(idx) > 1)
        starts = np.r_[idx[0], idx[splits + 1]]
        ends = np.r_[idx[splits], idx[-1]]
        total = 0.0
        for a, b in zip(starts, ends):
            dur = (b - a + 1) * WIN
            if dur >= SILENCE_MIN_S:
                add(a * WIN, "silence", f"Silence for {dur:.0f}s (below {SILENCE_DB:.0f} dBFS)", end=round(float((b + 1) * WIN), 2))
                total += dur
        stats["silence_seconds"] = round(total, 1)
    k = int(round(1.0 / WIN))
    sm = np.convolve(db, np.ones(k) / k, mode="same")
    typical = float(np.median(db[~quiet])) if (~quiet).any() else float(np.median(db))
    hot = (sm >= typical + PEAK_OVER_DB) & (sm >= PEAK_MIN_DB)
    peaks = []
    for i in np.flatnonzero(hot):
        if peaks and i * WIN - peaks[-1][1] * WIN <= PEAK_MERGE_S:
            if sm[i] > peaks[-1][2]:
                peaks[-1][2] = float(sm[i])
            peaks[-1][1] = i
        else:
            peaks.append([i, i, float(sm[i])])
    cap = int(PEAK_MAX_PER_HOUR * max(1.0, arc.duration / 3600))
    peaks = sorted(peaks, key=lambda p: -p[2])[:cap]
    for a, b, lvl in sorted(peaks):
        add(a * WIN, "loud_peak", f"Loud peak {lvl:.0f} dBFS (+{lvl - typical:.0f} dB over typical): shout, impact or music hit?")
    stats["loud_peaks"] = len(peaks)
    return ev, stats
