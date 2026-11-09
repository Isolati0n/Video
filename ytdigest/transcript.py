"""Stage 3: transcript.md with [HH:MM:SS] lines, from captions or faster-whisper.

  python -m ytdigest.transcript <video_id> <workdir> <outdir> [--archive-dir DIR]

Uses <workdir>/captions.vtt if present (manual or YouTube auto captions), otherwise
transcribes the archived audio with faster-whisper (small, int8, CPU).
Writes <outdir>/transcript.md and <outdir>/transcript.json.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess

import numpy as np
from pathlib import Path

from .common import hms, load_json, log
from .video import Archive

TS = re.compile(r"(\d+:)?(\d{2}):(\d{2})[.,](\d{3})")
TAG = re.compile(r"<[^>]+>")


def _sec(m) -> float:
    h = int(m.group(1)[:-1]) if m.group(1) else 0
    return h * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000


def parse_vtt(text: str) -> list[dict]:
    """Cues -> de-duplicated segments (YouTube auto captions repeat the previous line)."""
    segs, last = [], ""
    blocks = re.split(r"\n\s*\n", text.replace("\r", ""))
    for b in blocks:
        lines = b.strip().split("\n")
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        ms = list(TS.finditer(lines[idx]))
        if len(ms) < 2:
            continue
        start, end = _sec(ms[0]), _sec(ms[1])
        for raw in lines[idx + 1:]:
            clean = html.unescape(TAG.sub("", raw)).strip()
            if not clean or clean == last:
                continue
            t = clean[len(last):].strip() if last and clean.startswith(last) else clean
            last = clean
            if t:
                segs.append({"start": start, "end": end, "text": t})
    return segs


def group(segs: list[dict], max_span=15.0, max_chars=220) -> list[dict]:
    out, cur = [], None
    for s in segs:
        if cur and (s["start"] - cur["start"] > max_span or len(cur["text"]) > max_chars
                    or (cur["text"].endswith((".", "?", "!")) and len(cur["text"]) > 60)
                    or s["start"] - cur["end"] > 4):
            out.append(cur)
            cur = None
        if cur is None:
            cur = dict(s)
        else:
            cur["text"] += " " + s["text"]
            cur["end"] = max(cur["end"], s["end"])
    if cur:
        out.append(cur)
    return out


def whisper(arc: Archive, workdir: Path) -> tuple[list[dict], str]:
    from faster_whisper import WhisperModel

    model = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
    segs, lang = [], None
    for path, ps, _, _ in arc.parts_for(0, arc.duration):
        # decode with ffmpeg (16 kHz mono float32) instead of PyAV
        raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn",
                              "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], check=True,
                             capture_output=True).stdout
        audio = np.frombuffer(raw, np.float32)
        del raw
        it, info = model.transcribe(audio, beam_size=5, vad_filter=True,
                                    vad_parameters={"min_silence_duration_ms": 500},
                                    condition_on_previous_text=False)
        lang = lang or info.language
        for s in it:
            segs.append({"start": ps + s.start, "end": ps + s.end, "text": s.text.strip()})
            if len(segs) % 200 == 0:
                log(f"whisper: {hms(ps + s.end)} transcribed")
        del audio
    return segs, f"faster-whisper small int8 (language: {lang})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("workdir", type=Path)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--archive-dir", type=Path)
    a = ap.parse_args()
    a.outdir.mkdir(parents=True, exist_ok=True)
    info = load_json(a.workdir / "info.json", {}) or {}
    vtt = a.workdir / "captions.vtt"
    segs = []
    if vtt.exists():
        segs = parse_vtt(vtt.read_text(errors="replace"))
        ct = info.get("caption_track") or {}
        source = f"YouTube {'auto-generated' if ct.get('auto') else 'uploaded'} captions ({ct.get('lang', '?')})"
    if not segs:
        arc = Archive(a.video_id, a.workdir, a.archive_dir)
        segs, source = whisper(arc, a.workdir)
    grouped = group(segs)
    lines = [f"# Transcript: {info.get('title', a.video_id)}", "",
             f"Source: {source}. Timestamps are [HH:MM:SS] from the start of the video.", ""]
    lines += [f"[{hms(s['start'])}] {s['text']}" for s in grouped]
    (a.outdir / "transcript.md").write_text("\n".join(lines) + "\n")
    (a.outdir / "transcript.json").write_text(json.dumps({"source": source, "segments": grouped}))
    log(f"transcript: {len(grouped)} lines from {source}")


if __name__ == "__main__":
    main()
