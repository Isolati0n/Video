"""Contact sheets with burned-in timestamps (used by finalize and zoom)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .common import hms

MAX_BYTES = int(1.5 * 1000 * 1000) - 20_000
QUALITIES = (60, 52, 45, 38)


def label(img: np.ndarray, text: str, color=(255, 255, 255), border=None, min_scale: float = 0.0) -> np.ndarray:
    img = img.copy()
    scale = max(min_scale, img.shape[1] / 640 * 0.6)
    th = max(1, int(round(scale * 2)))
    (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
    cv2.rectangle(img, (0, 0), (tw + 12, tht + 14), (0, 0, 0), -1)
    cv2.putText(img, text, (6, tht + 7), cv2.FONT_HERSHEY_SIMPLEX, scale, color, th, cv2.LINE_AA)
    if border:
        cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), border, 4)
    return img


def save_jpeg(img: np.ndarray, path: Path, qualities=QUALITIES) -> int:
    # Quality 60 keeps text and numbers legible at ~190 KB per 1920x1080 sheet instead of ~360 KB at
    # 82; the hard cap is 1.5 MB.
    for q in qualities:
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


def tile_text(t: float, f: int | None = None, tag: str = "", fps: float = 25) -> str:
    """HH:MM:SS.ff fN: ff = frame within the second at the analysis rate, N = frame number."""
    s = hms(t)
    if f is not None:
        s += f".{f % max(1, int(round(fps))):02d} f{f}"
    return s + (f"  {tag}" if tag else "")
