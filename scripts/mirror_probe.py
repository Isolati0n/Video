"""Diagnostic for option 5: do public Invidious instances give a downloadable stream right now?

  python scripts/mirror_probe.py <video_id>

For each instance with the API enabled: GET /api/v1/videos/<id>, pick a muxed stream, and try to
read the first 1 MB of it. Prints one PROBE line per instance and a summary.
"""
import json
import sys
import urllib.request

UA = {"User-Agent": "yt-digest-diagnostic/1.0"}


def get(url, timeout=20, headers=None, limit=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit) if limit else r.read()


def main():
    vid = sys.argv[1]
    try:
        inst = json.loads(get("https://api.invidious.io/instances.json?sort_by=health"))
    except Exception as e:
        print(f"PROBE label=invidious-directory ok=0 err=\"{type(e).__name__}: {e}\"")
        return
    tried = ok = 0
    for name, meta in inst[:40]:
        if meta.get("type") != "https" or not meta.get("api"):
            continue
        base = meta["uri"].rstrip("/")
        tried += 1
        err, size = "", 0
        try:
            d = json.loads(get(f"{base}/api/v1/videos/{vid}", timeout=20))
            streams = d.get("formatStreams") or []
            if not streams:
                raise RuntimeError(d.get("error") or "no formatStreams")
            s = streams[-1]
            data = get(s["url"], timeout=30, headers={"Range": "bytes=0-1048575"}, limit=1048576)
            size = len(data)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:120]}".replace('"', "'")
        good = size >= 500_000
        ok += good
        print(f'PROBE label=invidious:{name} ok={int(good)} bytes={size} err="{err}"')
        if tried >= 20:
            break
    print(f"SUMMARY instances_tried={tried} streams_ok={ok}")


if __name__ == "__main__":
    main()
