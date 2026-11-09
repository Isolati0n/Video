"""Make a synthetic phone screen recording to test /ingest on the owner's own recordings.

  python tests/make_screen_recording.py <outdir> [seconds_per_part]

Writes rec-part1.mp4 and rec-part2.mp4: portrait 1080x2400, variable frame rate (only changed
frames are kept, like Android screen recorders), H.264, no audio, with known on-screen text.
Also writes expected_text.json (the lines each screen shows) to score OCR against.
"""
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 1080, 2400, 30
SCREENS = [
    ["Settings", "Wi-Fi: HomeNetwork-5G", "Battery 87%", "Storage 42 GB of 128 GB"],
    ["Step 1: open the Releases page", "Tap Draft a new release", "Choose a tag: my-video-1"],
    ["Step 2: attach the video file", "Maximum 2 GB per file", "Upload progress 64%"],
    ["Step 3: publish the release", "Comment /ingest my-video-1", "Digest ready in 5 minutes"],
    ["Meeting notes 14:30", "Action item: update the roadmap", "Owner: Alex Johnson, due Friday"],
    ["Total: $1,248.50", "Tax 8.25%", "Thank you for your order"],
]


def screen(lines, accent):
    img = np.full((H, W, 3), (24, 22, 20), np.uint8)
    cv2.rectangle(img, (0, 0), (W, 220), accent, -1)
    for i, line in enumerate(lines):
        cv2.putText(img, line, (60, 420 + i * 210), cv2.FONT_HERSHEY_SIMPLEX, 1.7, (235, 235, 235), 3, cv2.LINE_AA)
    cv2.circle(img, (W // 2, H - 220), 70, (90, 90, 90), 4)
    return img


def part(path: Path, screens, per_screen: float, offset: int):
    raw = path.with_suffix(".cfr.mp4")
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                          "-pix_fmt", "yuv420p", str(raw)], stdin=subprocess.PIPE)
    n = int(per_screen * FPS)
    for si, lines in enumerate(screens):
        base = screen(lines, (60 + 25 * ((si + offset) % 6), 90, 160))
        for f in range(n):
            img = base.copy()
            if f % 30 < 15:  # blinking cursor: a small change every half second
                cv2.rectangle(img, (60, 1800), (80, 1850), (235, 235, 235), -1)
            p.stdin.write(img.tobytes())
    p.stdin.close()
    p.wait()
    # keep only frames that changed: variable frame rate, like a phone screen recorder
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf", "mpdecimate=hi=64*40:lo=64*20:frac=0.1,setpts=N/FRAME_RATE/TB*0+PTS",
                    "-fps_mode", "vfr", "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)], check=True)
    raw.unlink()


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    per = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
    part(out / "rec-part1.mp4", SCREENS[:3], per, 0)
    part(out / "rec-part2.mp4", SCREENS[3:], per, 3)
    (out / "expected_text.json").write_text(json.dumps(SCREENS))


if __name__ == "__main__":
    main()
