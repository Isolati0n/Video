"""Stage 1+2: download with yt-dlp, re-encode to a 720p H.264 archive, split into
<1.9 GB parts and upload everything to a GitHub Release tagged with the video id.

If the release already holds parts.json the download is skipped (re-runs, zooms).

usage: python -m ytdigest.prepare <url-or-id> <workdir>
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .video import video_start
from .common import GH, gh_output, log, parse_video_id, save_json

MAX_PART = int(1.9 * 1024**3)
SEG_TARGET = int(1.75 * 1024**3)  # aim below the limit; keyframe cuts overshoot a little
BOT_MARKERS = ("confirm you're not a bot", "confirm you’re not a bot", "Sign in to confirm",
               "HTTP Error 403", "HTTP Error 429", "This content isn't available, try again later")


SELFTEST_ID = "SYNTHETIC01"


class Blocked(RuntimeError):
    pass


def ytdlp_base() -> list[str]:
    cmd = [sys.executable, "-m", "yt_dlp", "--no-progress", "--js-runtimes", "node",
           "--retries", "10", "--fragment-retries", "10", "--concurrent-fragments", "4"]
    cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if cookies:
        p = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "yt_cookies.txt"
        p.write_text(cookies + "\n")
        p.chmod(0o600)
        cmd += ["--cookies", str(p)]
        log("using YOUTUBE_COOKIES secret")
    return cmd


def run_ytdlp(args: list[str], cwd: Path) -> str:
    cmd = ytdlp_base() + args
    log("+ yt-dlp " + " ".join(args))
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = p.stdout + p.stderr
    sys.stderr.write(out[-6000:])
    if p.returncode != 0:
        if any(m.lower() in out.lower() for m in BOT_MARKERS):
            raise Blocked(out[-3000:])
        raise RuntimeError(f"yt-dlp failed ({p.returncode})")
    return out


def pick_caption(info: dict) -> tuple[str, bool] | None:
    """Return (language key, is_auto). Manual English > manual original language >
    auto 'orig' track (YouTube ASR in the spoken language) > auto English."""
    lang = (info.get("language") or "en").split("-")[0]
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    subs = {k: v for k, v in subs.items() if k != "live_chat"}
    for k in subs:
        if k == "en" or k.startswith("en-"):
            return k, False
    for k in subs:
        if k.split("-")[0] == lang:
            return k, False
    for k in (f"{lang}-orig", "en-orig", lang, "en"):
        if k in auto:
            return k, True
    return None


def ffprobe(path: Path) -> dict:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-print_format", "json",
                                   "-show_format", "-show_streams", str(path)])
    return json.loads(out)


def encode_archive(src: Path, dst: Path):
    """720p H.264 CRF 26, AAC 96k, keyframe every 2 s so zoom seeks are cheap."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats_period", "60", "-stats", "-y",
           "-i", str(src),
           "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", "scale=-2:'min(720,ih)':flags=bicubic",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
           "-g", "120", "-keyint_min", "25", "-sc_threshold", "0",
           "-c:a", "aac", "-b:a", "96k", "-ac", "2",
           "-movflags", "+faststart", str(dst)]
    log("+ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def frame_rate(path: Path) -> float:
    v = next(s for s in ffprobe(path)["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    return num / den


def count_frames(path: Path) -> int:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                                   "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
                                  text=True)
    return int(out.strip().split()[0])


def split_archive(full: Path, vid: str, outdir: Path, duration: float) -> list[dict]:
    size = full.stat().st_size
    if size <= MAX_PART:
        dst = outdir / f"{vid}.part000.mp4"
        full.rename(dst)
        return [{"name": dst.name, "start": 0.0, "end": duration, "size": dst.stat().st_size,
                 "vstart": video_start(dst)}]
    seg = max(60, int(duration * SEG_TARGET / size))
    lst = outdir / "segments.csv"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(full),
                    "-map", "0", "-c", "copy", "-f", "segment", "-segment_time", str(seg),
                    "-reset_timestamps", "1", "-segment_list", str(lst),
                    "-segment_list_type", "csv", "-segment_format_options", "movflags=+faststart",
                    str(outdir / f"{vid}.part%03d.mp4")], check=True)
    # Part start = number of video frames before it / frame rate. Exact for constant frame rate
    # and independent of container start offsets (B-frame delay, audio priming).
    fps = frame_rate(full)
    parts, nframes = [], 0
    for line in lst.read_text().splitlines():
        name = line.rsplit(",", 2)[0]
        p = outdir / name
        n = count_frames(p)
        parts.append({"name": name, "start": round(nframes / fps, 6), "end": round((nframes + n) / fps, 6),
                      "frames": n, "size": p.stat().st_size, "vstart": video_start(p)})
        nframes += n
        if p.stat().st_size > MAX_PART:
            raise RuntimeError(f"{name} is {p.stat().st_size} bytes > 1.9 GB")
    full.unlink()
    lst.unlink()
    return parts


def chunk_plan(duration: float, chunk_len: float) -> list[dict]:
    n = max(1, math.ceil(duration / chunk_len))
    step = duration / n
    return [{"idx": i, "start": round(i * step, 3), "end": round(min(duration, (i + 1) * step), 3)}
            for i in range(n)]


def setup(argv):
    mode, target, work = argv[1], argv[2], Path(argv[3])
    vid = parse_video_id(target)
    if not vid:
        raise SystemExit(f"no YouTube video id found in {target!r}")
    work.mkdir(parents=True, exist_ok=True)
    return mode, vid, work


def plan(vid: str, work: Path, gh: GH, chunk_len: float):
    """Step 1: is the video already archived in its Release? Then skip download + encode."""
    gh_output(video_id=vid)
    rel = gh.release(vid)
    if rel and any(a["name"] == "parts.json" for a in rel["assets"]):
        log(f"release {vid} already archived; skipping download")
        gh.download_asset(rel, "parts.json", work / "parts.json")
        for name in ("info.json", "captions.vtt"):
            if any(a["name"] == name for a in rel["assets"]):
                gh.download_asset(rel, name, work / name)
        manifest = json.loads((work / "parts.json").read_text())
        gh_output(chunks=chunk_plan(manifest["duration"], chunk_len), duration=str(manifest["duration"]),
                  has_captions="true" if (work / "captions.vtt").exists() else "false", reused="true")
    else:
        gh_output(reused="false")


def fetch(vid: str, work: Path):
    """Step 2: get the source video + metadata + captions into work/dl (the only step that
    talks to YouTube, so it is the only one run through the WARP tunnel)."""
    url = f"https://www.youtube.com/watch?v={vid}"
    dl = work / "dl"
    dl.mkdir(exist_ok=True)
    cap = None
    if vid == SELFTEST_ID:  # offline self-test: synthetic clip with known orb values, no YouTube
        secs = os.environ.get("SELFTEST_SECONDS", "90")
        subprocess.run([sys.executable, "tests/synthetic.py", str(dl / "syn"), secs], check=True)
        (dl / "syn" / f"{SELFTEST_ID}.part000.mp4").rename(dl / "src.mp4")
        info = json.loads((dl / "syn" / "info.json").read_text())
        info.update(channel="yt-digest self-test", duration=float(secs), webpage_url="(synthetic)")
    else:
        info, cap = download(url, dl, work)
    slim = {k: info.get(k) for k in ("id", "title", "channel", "uploader", "upload_date", "duration",
                                     "description", "webpage_url", "chapters", "language", "view_count")}
    save_json(dl / "meta.json", {"info": slim, "cap": cap})


def download(url: str, dl: Path, work: Path):
    try:
        run_ytdlp(["--skip-download", "--write-info-json", "-o", "src.%(ext)s", url], dl)
        info = json.loads((dl / "src.info.json").read_text())
        cap = pick_caption(info)
        args = ["--load-info-json", "src.info.json", "-f",
                "bv*[height<=720][vcodec^=avc1]+ba/bv*[height<=720]+ba/b[height<=720]/b",
                "--merge-output-format", "mkv", "-o", "src.%(ext)s"]
        if cap:
            args += ["--write-auto-subs" if cap[1] else "--write-subs", "--sub-langs", cap[0],
                     "--sub-format", "vtt/best", "--convert-subs", "vtt"]
        run_ytdlp(args, dl)
    except Blocked as e:
        gh_output(blocked="true")
        (work / "blocked.txt").write_text(str(e))
        log("YouTube blocked this attempt (bot check / HTTP 403 / 429)")
        raise SystemExit(75)  # EX_TEMPFAIL: the caller may retry from a fresh network path
    return info, cap


def archive(vid: str, work: Path, gh: GH, chunk_len: float):
    """Step 3: re-encode to 720p H.264, split <1.9 GB, upload to the Release."""
    url = f"https://www.youtube.com/watch?v={vid}"
    dl = work / "dl"
    meta = json.loads((dl / "meta.json").read_text())
    slim, cap = meta["info"], meta["cap"]
    if vid == SELFTEST_ID:
        chunk_len = 30.0  # several matrix jobs
        global MAX_PART, SEG_TARGET
        MAX_PART, SEG_TARGET = 3 * 1024**2, 2 * 1024**2  # several release parts
    src = next(p for p in dl.iterdir() if p.name.startswith("src.") and p.suffix in (".mkv", ".mp4", ".webm"))
    subs = [p for p in dl.iterdir() if p.suffix == ".vtt"]
    if subs:
        shutil.copy(subs[0], work / "captions.vtt")
    slim["caption_track"] = {"lang": cap[0], "auto": cap[1]} if cap else None
    save_json(work / "info.json", slim)

    arch = work / "archive"
    arch.mkdir(exist_ok=True)
    full = arch / f"{vid}.mp4"
    encode_archive(src, full)
    src.unlink()
    pr = ffprobe(full)
    v = next(s for s in pr["streams"] if s["codec_type"] == "video")
    duration = float(pr["format"]["duration"])
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    parts = split_archive(full, vid, arch, duration)
    manifest = {"video_id": vid, "duration": duration, "width": v["width"], "height": v["height"],
                "fps": num / den, "parts": parts}
    save_json(work / "parts.json", manifest)

    body = (f"Archive of [{slim['title']}]({url}) by {slim.get('channel')}\n\n"
            f"720p H.264 CRF 26, {len(parts)} part(s). Parts are independently playable; "
            f"`parts.json` lists the start time of each.")
    gh.ensure_release(vid, f"{vid}: {slim['title']}"[:120], body)
    for p in parts:
        log(f"uploading {p['name']} ({p['size'] / 1e6:.0f} MB)")
        gh.upload_asset(gh.release(vid), arch / p["name"], "video/mp4")
    for name in ("info.json", "captions.vtt", "parts.json"):  # parts.json last = archive complete
        if (work / name).exists():
            gh.upload_asset(gh.release(vid), work / name)
    gh_output(chunks=chunk_plan(duration, chunk_len), duration=str(duration),
              has_captions="true" if cap else "false", reused="false")


def main():
    mode, vid, work = setup(sys.argv)
    chunk_len = float(os.environ.get("CHUNK_SECONDS", "1200"))
    if mode == "plan":
        plan(vid, work, GH(), chunk_len)
    elif mode == "download":
        fetch(vid, work)
    elif mode == "archive":
        archive(vid, work, GH(), chunk_len)
    else:
        raise SystemExit(f"unknown mode {mode!r}; use plan | download | archive")


if __name__ == "__main__":
    main()
