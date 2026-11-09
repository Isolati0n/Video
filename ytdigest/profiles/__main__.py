"""python -m ytdigest.profiles                      list the registered profiles
   python -m ytdigest.profiles check <name> <video.mp4> [config.json] [seconds]
        run a profile's analyzer on the first seconds of a local video and print what it produced
        (use it while writing a new profile; it needs no GitHub and no archive)
"""
import json
import sys
from collections import Counter
from pathlib import Path

import cv2

from . import analysis_fps, get, names


def main():
    if len(sys.argv) < 2:
        for n in names():
            p = get(n)
            print(f"{n:10s} fps={'native' if not p.fps else p.fps:<7} {p.title}: {p.description}")
        return
    if sys.argv[1] != "check" or len(sys.argv) < 4:
        raise SystemExit(__doc__)
    prof, video = get(sys.argv[2]), Path(sys.argv[3])
    cfg = json.loads(Path(sys.argv[4]).read_text()) if len(sys.argv) > 4 and sys.argv[4].endswith(".json") else prof.defaults()
    secs = float(sys.argv[5]) if len(sys.argv) > 5 else 20.0
    cap = cv2.VideoCapture(str(video))
    native = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = analysis_fps(native, prof)
    step = max(1, round(native / fps))
    an = prof.analyzer(cfg, W, H, fps)
    recs, k = [], 0
    i = 0
    while i / native < secs:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            small = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            rec = {"f": k, "t": round(k / fps, 3)}
            if an is not None:
                an.process(frame, gray, k, k / fps, rec)
            rec.pop("_ocr_crops", None)
            recs.append(rec)
            k += 1
        i += 1
    print(f"{prof.name}: {len(recs)} frames at {fps:g} fps ({W}x{H}); keys per frame: {sorted(recs[0]) if recs else []}")
    for r in recs[:3]:
        print("  ", r)
    ev, stats = prof.derive_events(recs, fps, cfg)
    print("events:", dict(Counter(e["type"] for e in ev)), "stats:", stats)


main()
