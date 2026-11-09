"""Diagnostic: can yt-dlp fetch a video from this machine without any login?

  python scripts/access_probe.py <label> <video_id> [extra yt-dlp args...]

Prints exactly one machine-readable line, always exits 0, so a matrix of variants can be
compared from the job logs:

  PROBE label=<label> stage=<ok|metadata|download> ok=<0|1> secs=<n> bytes=<n> ip=<egress ip> err="<first error line>"
"""
import glob
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request


def egress_ip() -> str:
    try:
        return urllib.request.urlopen("https://api.ipify.org", timeout=15).read().decode().strip()
    except Exception as e:  # diagnostic only
        return f"unknown({type(e).__name__})"


def main():
    label, vid, extra = sys.argv[1], sys.argv[2], sys.argv[3:]
    url = f"https://www.youtube.com/watch?v={vid}"
    base = [sys.executable, "-m", "yt_dlp", "--no-progress", "--js-runtimes", "node", "--retries", "3",
            "--socket-timeout", "30", *extra]
    t0 = time.time()
    stage, err = "ok", ""
    with tempfile.TemporaryDirectory() as d:
        p = subprocess.run(base + ["--simulate", "--print", "%(id)s %(duration)s", url],
                           capture_output=True, text=True, timeout=300)
        out = p.stdout + p.stderr
        print(out[-1500:], file=sys.stderr)
        if p.returncode != 0:
            stage = "metadata"
            err = next((l for l in out.splitlines() if "ERROR" in l), out.strip()[-200:])
        else:
            fmt = "bv*[height<=720][vcodec^=avc1]+ba/bv*[height<=720]+ba/b[height<=720]/b"
            p = subprocess.run(base + ["-f", fmt, "--merge-output-format", "mkv", "-o", f"{d}/v.%(ext)s", url],
                               capture_output=True, text=True, timeout=1500)
            out = p.stdout + p.stderr
            print(out[-2500:], file=sys.stderr)
            if p.returncode != 0:
                stage = "download"
                err = next((l for l in out.splitlines() if "ERROR" in l), out.strip()[-200:])
        size = sum(os.path.getsize(f) for f in glob.glob(f"{d}/v.*"))
    err = re.sub(r"\s+", " ", err)[:220].replace('"', "'")
    ok = int(stage == "ok" and size > 100_000)
    line = (f'PROBE label={label} stage={stage} ok={ok} secs={time.time() - t0:.0f} bytes={size} '
            f'ip={egress_ip()} err="{err}"')
    print(line)
    print(f"::notice title=PROBE {label}::{line}")  # readable later via the check-run annotations API


if __name__ == "__main__":
    main()
