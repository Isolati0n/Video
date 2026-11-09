"""Find a corner region that moves on its own (a webcam / picture-in-picture) so it can be masked out
of the picture-change measures.

Why: a presenter's face moves constantly, so the perceptual hash of a slide that has not changed keeps
changing, and the same slide gets several keyframes. A *static* overlay (logo, watermark) cannot make two
identical frames look different, so only moving regions need masking.

Method: look at ~10 short bursts spread over the video (10 fps), average the frame-to-frame change per
cell of a 16x9 grid, and take cells that are far more active than the typical cell. A connected group of
such cells that touches a frame corner, is active in most bursts and covers < 35 % of the frame is the
region. Whole-frame motion (gameplay, camera pans) produces no such region.
"""
from __future__ import annotations

import cv2
import numpy as np

GX, GY = 16, 9


def detect(arc, bursts: int = 10, secs: float = 1.5, fps: float = 10.0) -> dict | None:
    d = arc.duration
    if d < 20:
        return None
    maps = []
    for t in np.linspace(0.05 * d, 0.95 * d - secs, bursts):
        prev, acc, n = None, np.zeros((GY, GX), np.float32), 0
        for _, f in arc.frames(float(t), float(t) + secs, fps, (320, 180)):
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
            if prev is not None:
                acc += cv2.resize(np.abs(g - prev), (GX, GY), interpolation=cv2.INTER_AREA)
                n += 1
            prev = g
        if n:
            maps.append(acc / n)
    if len(maps) < 5:
        return None
    M = np.stack(maps)                       # bursts x rows x cols
    A = np.median(M, 0)
    base = float(np.median(A))
    active = A >= max(0.6, 5 * base)
    if active.mean() > 0.35 or active.sum() < 6:
        return None
    n_lab, lab = cv2.connectedComponents(active.astype(np.uint8), connectivity=4)
    best = None
    for i in range(1, n_lab):
        ys, xs = np.nonzero(lab == i)
        if len(ys) < 6:
            continue
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        touches_x = x0 <= 1 or x1 >= GX - 2
        touches_y = y0 <= 1 or y1 >= GY - 2
        area = (x1 - x0 + 1) * (y1 - y0 + 1)
        if not (touches_x and touches_y) or area > 0.35 * GX * GY:
            continue
        presence = float((M[:, ys, xs] >= 0.4).mean())
        if presence < 0.6:
            continue
        score = len(ys) * presence
        if best is None or score > best[0]:
            best = (score, x0, x1, y0, y1, presence)
    if best is None:
        return None
    _, x0, x1, y0, y1, presence = best
    x0, y0 = max(0, x0 - 1), max(0, y0 - 1)          # a cell of margin
    x1, y1 = min(GX - 1, x1 + 1), min(GY - 1, y1 + 1)
    x0, x1, y0, y1 = int(x0), int(x1), int(y0), int(y1)
    return {"rect": [round(x0 / GX, 4), round(y0 / GY, 4), round((x1 - x0 + 1) / GX, 4), round((y1 - y0 + 1) / GY, 4)],
            "kind": "moving corner region (webcam / picture-in-picture)", "presence": round(float(presence), 2),
            "activity_vs_typical": round(float(A[y0:y1 + 1, x0:x1 + 1].max()) / max(base, 0.05), 1)}


def mask_rect(rect: list[float], w: int, h: int) -> tuple[int, int, int, int]:
    x, y, rw, rh = rect
    return int(round(x * w)), int(round(y * h)), int(round((x + rw) * w)), int(round((y + rh) * h))
