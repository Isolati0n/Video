"""Generate a synthetic D2R-like clip with known life/mana values to test the pipeline offline.

  python tests/synthetic.py <outdir> [seconds]

Writes <outdir>/SYNTHETIC01.part000.mp4, parts.json and truth.json (life/mana per 25 fps frame).
Script: full life, a hit (-40% in 0.3 s), rejuv refill, poison (green orb), a 2 s loading
screen, teleport-like jumps, death (empty orb + 'You have died' text).
"""
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 1280, 720, 25


def life_mana(t):
    life, mana, poison = 100.0, 100.0, False
    if 4 <= t < 4.3: life = 100 - (t - 4) / 0.3 * 40
    elif 4.3 <= t < 6: life = 60
    elif 6 <= t < 6.12: life = 60 + (t - 6) / 0.12 * 40
    if 3 <= t < 8: mana = max(20, 100 - (t - 3) * 16)
    if 8 <= t < 8.2: mana = 20 + (t - 8) / 0.2 * 80
    if 10 <= t < 13: life, poison = 100 - (t - 10) * 10, True
    if 13 <= t < 16: life = 70
    if 19 <= t < 20: life = max(0, 70 - (t - 19) * 70)
    if t >= 20: life, mana = 0, 50
    return life, mana, poison


def draw_orb(img, cx, cy, r, level, color):
    cv2.circle(img, (cx, cy), r + 6, (70, 90, 110), 6)
    cv2.circle(img, (cx, cy), r, (15, 12, 10), -1)
    top = int(cy + r - 2 * r * level / 100)
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.circle(mask, (cx, cy), r, 255, -1)
    mask[:max(0, top)] = 0
    img[mask > 0] = color
    cv2.ellipse(img, (cx - r // 3, cy - r // 3), (r // 4, r // 7), -30, 0, 360, (200, 200, 200), -1)  # glare


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 24
    rng = np.random.default_rng(1)
    world = rng.integers(20, 90, (H * 3, W * 3, 3), dtype=np.uint8)
    world = cv2.GaussianBlur(world, (0, 0), 6)
    world = cv2.addWeighted(world, 1.0, rng.integers(0, 30, world.shape, dtype=np.uint8), 0.5, 0)
    path = out / "SYNTHETIC01.part000.mp4"
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
                          "-s", f"{W}x{H}", "-r", "50", "-i", "-",
                          "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-shortest",
                          "-c:a", "aac", "-c:v", "libx264", "-crf", "26",
                          "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
    truth = []
    cam = np.array([W, H], float)
    for i in range(int(secs * 50)):
        t = i / 50
        if 14 <= t % 24 < 15:  # teleport chain
            if i % 10 == 0: cam += rng.integers(-300, 300, 2)
        else:
            cam += [1.5, 0.7]
        cam = np.clip(cam, 0, [2 * W - 1, 2 * H - 1])
        x, y = int(cam[0]), int(cam[1])
        img = world[y:y + H, x:x + W].copy()
        life, mana, poison = life_mana(t % 24)
        tl = t % 24
        if 16.5 <= tl < 18.5:  # loading screen
            img[:] = 5
            cv2.putText(img, "Loading", (560, 360), cv2.FONT_HERSHEY_SIMPLEX, 1, (60, 60, 60), 2)
        else:
            cv2.rectangle(img, (0, 610), (W, H), (40, 50, 60), -1)
            for k in range(8):
                cv2.rectangle(img, (430 + k * 54, 650), (478 + k * 54, 698), (90 + k * 15, 120, 160), -1)
                cv2.rectangle(img, (430 + k * 54, 650), (478 + k * 54, 698), (200, 200, 200), 2)
            draw_orb(img, 140, 640, 70, life, (20, 160, 40) if poison else (20, 20, 190))
            draw_orb(img, W - 140, 640, 70, mana, (190, 60, 20))
            if tl >= 20.5:
                cv2.putText(img, "You have died", (470, 280), cv2.FONT_HERSHEY_TRIPLEX, 1.6, (40, 40, 220), 2)
            if 11 <= tl < 13:
                cv2.putText(img, "Player1 joined our world. Diablo's minions grow stronger.",
                            (20, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (60, 220, 230), 1)
        p.stdin.write(img.tobytes())
        if i % 2 == 0:
            off = 16.5 <= t % 24 < 18.5
            truth.append({"f": i // 2, "life": None if off else round(life, 1),
                          "mana": None if off else round(mana, 1)})
    p.stdin.close(); p.wait()
    (out / "parts.json").write_text(json.dumps({"video_id": "SYNTHETIC01", "duration": secs, "width": W,
                                                "height": H, "fps": 50, "parts": [
        {"name": path.name, "start": 0.0, "end": secs, "size": path.stat().st_size}]}))
    (out / "truth.json").write_text(json.dumps(truth))
    (out / "info.json").write_text(json.dumps({"title": "Synthetic test clip", "id": "SYNTHETIC01"}))


if __name__ == "__main__":
    main()
