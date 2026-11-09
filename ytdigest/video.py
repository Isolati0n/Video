"""Access to the archived video (from the GitHub Release or a local dir) and frame readers."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from .common import GH, FPS, log


class Archive:
    def __init__(self, video_id: str, workdir: Path, local_dir: Path | None = None):
        self.vid = video_id
        self.work = Path(workdir)
        self.work.mkdir(parents=True, exist_ok=True)
        self.local = Path(local_dir) if local_dir else None
        self._gh = None
        self._rel = None
        mp = (self.local / "parts.json") if self.local and (self.local / "parts.json").exists() else None
        if mp is None:
            mp = self.work / "parts.json"
            if not mp.exists():
                self.gh_download("parts.json", mp)
        self.manifest = json.loads(mp.read_text())
        self.duration = self.manifest["duration"]
        self.width = self.manifest["width"]
        self.height = self.manifest["height"]

    def gh_download(self, name: str, dest: Path):
        if self._gh is None:
            self._gh = GH()
            self._rel = self._gh.release(self.vid)
            if not self._rel:
                raise SystemExit(f"No release '{self.vid}' found. Run the digest for this video first.")
        return self._gh.download_asset(self._rel, name, dest)

    def part_path(self, part: dict) -> Path:
        if self.local and (self.local / part["name"]).exists():
            return self.local / part["name"]
        dest = self.work / part["name"]
        log(f"fetching {part['name']} ({part['size'] / 1e6:.0f} MB) from release")
        return self.gh_download(part["name"], dest)

    def parts_for(self, start: float, end: float) -> list[tuple[Path, float, float]]:
        """[(path, part_start, part_end)] covering [start, end)."""
        out = []
        for p in self.manifest["parts"]:
            if p["end"] > start and p["start"] < end:
                out.append((self.part_path(p), p["start"], p["end"]))
        return out

    def frames(self, start: float, end: float, fps: float = FPS, size: tuple[int, int] | None = None):
        """Yield (t, frame_bgr) on a fixed fps grid t = k/fps for start <= t < end."""
        w, h = size or (self.width, self.height)
        k0 = int(np.ceil(start * fps - 1e-6))
        k_end = int(np.ceil(end * fps - 1e-6))
        k = k0
        for path, ps, pe in self.parts_for(start, end):
            if k >= k_end:
                break
            seg_end = min(end, pe)
            t_local = max(0.0, k / fps - ps)
            n = int(np.ceil(seg_end * fps - 1e-6)) - k
            if n <= 0:
                continue
            for i, frame in enumerate(read_frames(path, t_local, n, fps, w, h, scale=size is not None)):
                yield (k + i) / fps, frame
            k += n

    def frame_at(self, t: float, size=None) -> np.ndarray | None:
        k = int(round(t * FPS))
        for _, f in self.frames(k / FPS, (k + 1) / FPS, FPS, size):
            return f
        return None


def read_frames(path: Path, t_start: float, count: int, fps: float, w: int, h: int, scale: bool = False):
    """Decode `count` frames starting at t_start (seconds, local to the file) resampled to fps."""
    vf = f"fps={fps}:start_time=0:round=near"
    if scale:
        vf += f",scale={w}:{h}:flags=area"
    seek_pad = min(t_start, 3.0)  # fast seek to just before, then exact trim with -ss after -i
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
           "-ss", f"{t_start - seek_pad:.4f}", "-i", str(path), "-ss", f"{seek_pad:.4f}",
           "-an", "-sn", "-vf", vf, "-frames:v", str(count),
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * 3 * 4)
    fsz = w * h * 3
    got = 0
    last = None
    try:
        while got < count:
            buf = proc.stdout.read(fsz)
            if len(buf) < fsz:
                break
            last = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            got += 1
            yield last
    finally:
        proc.stdout.close()
        proc.wait()
    # pad with the last frame if the container ended a frame or two early
    while last is not None and got < count:
        got += 1
        yield last
