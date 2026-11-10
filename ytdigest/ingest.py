"""Fallback intake when YouTube cannot be downloaded (or for the owner's own recordings).

  python -m ytdigest.ingest <video_id> <source> <workdir>

<source> is either
  - a Release tag in this repo whose assets are the uploaded video file(s) (phone upload), or
  - a direct http(s) URL to a video file.
The file(s) are normalised into <workdir>/dl/src.mkv plus dl/meta.json, exactly where the YouTube
download leaves its result, so `ytdigest.prepare archive` and everything after it is unchanged.

Several assets (a video split because of the 2 GB asset limit) are joined in name order.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

from .common import GH, gh_output, log, save_json

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".ts", ".3gp")
OURS = re.compile(r"(\.part\d{3}\.mp4$|^parts\.json$|^info\.json$)")
MAX_BYTES = 12 * 1024**3
RESERVED_PREFIXES = ("sheets-", "zoom-")


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ------------------------------------------------------------------ safe URL download
def _check_host(host: str):
    """Refuse loopback, private, link-local and metadata addresses (the runner must not be used to
    reach internal services)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise SystemExit(f"cannot resolve {host}: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise SystemExit(f"refusing {host}: resolves to a non-public address ({ip})")


def download_url(url: str, dest: Path) -> Path:
    s = requests.Session()
    s.headers["User-Agent"] = "yt-digest-ingest/1.0"
    cur = url
    for _ in range(6):
        u = urlparse(cur)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise SystemExit(f"only http(s) file URLs are accepted: {cur!r}")
        _check_host(u.hostname)
        r = s.get(cur, stream=True, allow_redirects=False, timeout=60)
        if r.status_code in (301, 302, 303, 307, 308):
            cur = requests.compat.urljoin(cur, r.headers.get("Location", ""))
            r.close()
            continue
        r.raise_for_status()
        size = int(r.headers.get("Content-Length") or 0)
        if size > MAX_BYTES:
            raise SystemExit(f"file is {size / 1e9:.1f} GB; the limit is {MAX_BYTES / 1e9:.0f} GB")
        done = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(8 << 20):
                done += len(chunk)
                if done > MAX_BYTES:
                    raise SystemExit("download exceeded the size limit")
                fh.write(chunk)
        log(f"downloaded {done / 1e6:.0f} MB from {u.hostname}")
        return dest
    raise SystemExit("too many redirects")


# ------------------------------------------------------------------ helpers
def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"{path.name} is not a readable video file: {out.stderr.strip()[:200]}")
    d = json.loads(out.stdout)
    if not any(s.get("codec_type") == "video" for s in d.get("streams", [])):
        raise SystemExit(f"{path.name} has no video stream")
    return d


def join(files: list[Path], dest: Path) -> Path:
    """Concatenate split uploads. Stream copy when the parts are compatible, else re-encode each part."""
    lst = dest.parent / "concat.txt"
    lst.write_text("".join(f"file '{f.resolve()}'\n" for f in files))
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-map", "0:v:0", "-an", "-c", "copy", str(dest)], capture_output=True, text=True)
    want = sum(float(probe(f)["format"]["duration"]) for f in files)
    if r.returncode == 0 and abs(float(probe(dest)["format"]["duration"]) - want) < max(2.0, 0.01 * want):
        return dest
    log("parts are not stream-compatible; normalising each before joining")
    normed = []
    for i, f in enumerate(files):
        n = dest.parent / f"norm_{i:03d}.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(f), "-map", "0:v:0", "-vf",
                        "scale=-2:'min(720,ih)'", "-r", "30", "-c:v", "libx264", "-crf", "23", "-preset", "veryfast",
                        "-an", str(n)], check=True)
        normed.append(n)
    lst.write_text("".join(f"file '{n.resolve()}'\n" for n in normed))
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-map", "0:v:0", "-an", "-c", "copy", str(dest)], check=True)
    return dest


def run(vid: str, source: str, work: Path) -> None:
    dl = work / "dl"
    dl.mkdir(parents=True, exist_ok=True)
    info: dict = {"id": vid, "channel": "uploaded file", "webpage_url": None}
    if re.match(r"https?://", source, re.I):
        name = Path(urlparse(source).path).name or vid
        raw = dl / "in_000"
        download_url(source, raw)
        files = [raw]
        info.update(title=name, description=f"Fetched from {urlparse(source).hostname}")
    else:
        if source.startswith(RESERVED_PREFIXES):
            raise SystemExit(f"tag {source!r} is reserved for generated releases; use another tag")
        gh = GH()
        rel = gh.release(source)
        if not rel:
            raise SystemExit(f"no Release with tag '{source}' in this repo. Create it (Releases → Draft a new release) "
                             f"with the tag '{source}', attach the video file(s) and publish, then comment /ingest again.")
        assets = sorted((a for a in rel["assets"] if a["name"].lower().endswith(VIDEO_EXT) and not OURS.search(a["name"])),
                        key=lambda a: natural_key(a["name"]))
        if not assets:
            raise SystemExit(f"Release '{source}' has no video asset ({', '.join(VIDEO_EXT)}). "
                             f"Attach the file to the release and comment /ingest again.")
        files = []
        for i, a in enumerate(assets):
            log(f"fetching asset {a['name']} ({a['size'] / 1e6:.0f} MB)")
            files.append(gh.download_asset(rel, a["name"], dl / f"in_{i:03d}{Path(a['name']).suffix.lower()}"))
        info.update(title=rel.get("name") or source, description=rel.get("body") or "",
                    channel=f"uploaded ({len(assets)} file{'s' if len(assets) > 1 else ''})")
    for f in files:
        probe(f)  # fail early on anything that is not a video
    src = files[0] if len(files) == 1 else join(files, dl / "src_joined.mkv")
    final = dl / ("src" + (src.suffix if src.suffix in (".mkv", ".mp4", ".webm") else ".mkv"))
    if src != final:
        src.rename(final)
    for f in files:
        if f.exists() and f != final:
            f.unlink()
    d = probe(final)
    info["duration"] = float(d["format"]["duration"])
    slim = {k: info.get(k) for k in ("id", "title", "channel", "description", "webpage_url", "duration")}
    save_json(dl / "meta.json", {"info": slim})
    log(f"ingested {vid}: {slim['title']} ({info['duration']:.0f}s) from {'URL' if re.match(r'https?://', source) else 'Release ' + source}")
    gh_output(ingested="true")


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
