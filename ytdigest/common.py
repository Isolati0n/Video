"""Shared helpers: paths, timestamps, GitHub API, config."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
FPS = 25  # D2R game tick rate; analysis runs on every frame at this rate
VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def parse_video_id(text: str) -> str | None:
    m = VIDEO_ID_RE.search(text or "")
    if m:
        return m.group(1)
    m = re.fullmatch(r"\s*([A-Za-z0-9_-]{11})\s*", text or "")
    return m.group(1) if m else None


def hms(t: float, frac: bool = False) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    out = f"{h:02d}:{m:02d}:{s:02d}"
    if frac:
        out += f".{int(round((t - int(t)) * FPS)):02d}f"  # frame within second
    return out


def parse_ts(s: str) -> float:
    """'1:23:10', '23:10', '83.5', '1:23:10.5' -> seconds."""
    parts = s.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"bad timestamp {s!r}")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def digest_dir(video_id: str) -> Path:
    return ROOT / "digests" / video_id


def hud_config_path(video_id: str) -> Path:
    return ROOT / "config" / "hud" / f"{video_id}.json"


def load_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text())
    except FileNotFoundError:
        return default


def save_json(p: Path, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2) + "\n")


# ---------------------------------------------------------------- GitHub API
class GH:
    def __init__(self):
        self.token = os.environ["GITHUB_TOKEN"]
        self.repo = os.environ["GITHUB_REPOSITORY"]
        self.api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _req(self, method, url, **kw):
        for attempt in range(5):
            r = self.s.request(method, url, timeout=kw.pop("timeout", 120), **kw)
            if r.status_code < 500 and r.status_code != 429:
                return r
            log(f"GitHub {method} {url} -> {r.status_code}, retrying")
            time.sleep(2 ** (attempt + 1))
        return r

    def comment(self, issue: int | str, body: str):
        r = self._req("POST", f"{self.api}/repos/{self.repo}/issues/{issue}/comments",
                      json={"body": body[:65000]})
        r.raise_for_status()

    def release(self, tag: str):
        r = self._req("GET", f"{self.api}/repos/{self.repo}/releases/tags/{tag}")
        return r.json() if r.status_code == 200 else None

    def ensure_release(self, tag: str, name: str, body: str):
        rel = self.release(tag)
        if rel:
            return rel
        r = self._req("POST", f"{self.api}/repos/{self.repo}/releases",
                      json={"tag_name": tag, "name": name, "body": body[:100000]})
        r.raise_for_status()
        return r.json()

    def upload_asset(self, rel: dict, path: Path, content_type="application/octet-stream"):
        path = Path(path)
        for a in rel.get("assets", []):
            if a["name"] == path.name:
                self._req("DELETE", a["url"]).raise_for_status()
        upload = rel["upload_url"].split("{")[0]
        size = path.stat().st_size
        for attempt in range(4):
            with open(path, "rb") as fh:
                r = self.s.post(upload, params={"name": path.name}, data=fh, timeout=3600,
                                headers={"Content-Type": content_type, "Content-Length": str(size)})
            if r.status_code in (200, 201):
                return r.json()
            log(f"upload {path.name} failed {r.status_code}: {r.text[:300]}")
            time.sleep(5 * (attempt + 1))
        r.raise_for_status()

    def download_asset(self, rel: dict, name: str, dest: Path):
        asset = next((a for a in rel["assets"] if a["name"] == name), None)
        if not asset:
            raise FileNotFoundError(f"asset {name} not in release {rel['tag_name']}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size == asset["size"]:
            return dest
        for attempt in range(4):
            try:
                with self.s.get(asset["url"], headers={"Accept": "application/octet-stream"},
                                stream=True, timeout=600) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as fh:
                        for chunk in r.iter_content(8 << 20):
                            fh.write(chunk)
                if dest.stat().st_size == asset["size"]:
                    return dest
            except requests.RequestException as e:
                log(f"download {name} failed: {e}")
            time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"could not download {name}")


def gh_output(**kv):
    """Write step outputs for GitHub Actions."""
    out = os.environ.get("GITHUB_OUTPUT")
    lines = []
    for k, v in kv.items():
        v = v if isinstance(v, str) else json.dumps(v)
        lines.append(f"{k}={v}")
    if out:
        with open(out, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))
