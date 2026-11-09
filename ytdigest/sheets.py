"""Contact sheets with burned-in timestamps (used by finalize and zoom)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .common import FPS, hms

MAX_BYTES = int(1.5 * 1000 * 1000) - 20_000


def label(img: np.ndarray, text: str, color=(255, 255, 255), border=None) -> np.ndarray:
    img = img.copy()
    scale = img.shape[1] / 640 * 0.6
    th = max(1, int(round(scale * 2)))
    (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
    cv2.rectangle(img, (0, 0), (tw + 12, tht + 14), (0, 0, 0), -1)
    cv2.putText(img, text, (6, tht + 7), cv2.FONT_HERSHEY_SIMPLEX, scale, color, th, cv2.LINE_AA)
    if border:
        cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), border, 4)
    return img


def save_jpeg(img: np.ndarray, path: Path) -> int:
    for q in (82, 75, 68, 60, 52, 45, 38):
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q, cv2.IMWRITE_JPEG_OPTIMIZE, 1])
        if len(buf) <= MAX_BYTES:
            break
    path.write_bytes(buf.tobytes())
    return len(buf)


def grid(tiles: list[np.ndarray], cols: int, rows: int) -> np.ndarray:
    h, w = tiles[0].shape[:2]
    out = np.zeros((h * rows, w * cols, 3), np.uint8)
    for i, t in enumerate(tiles[:cols * rows]):
        r, c = divmod(i, cols)
        out[r * h:(r + 1) * h, c * w:(c + 1) * w] = t
    return out


def tile_text(t: float, f: int | None = None, tag: str = "") -> str:
    s = hms(t)
    if f is not None:
        s += f".{f % FPS:02d} f{f}"
    return s + (f"  {tag}" if tag else "")
