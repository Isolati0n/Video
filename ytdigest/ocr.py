"""Tesseract OCR for D2R text overlays (chat, centre messages, area name)."""
from __future__ import annotations

import os
import re

import cv2
import numpy as np
import pytesseract

os.environ.setdefault("OMP_THREAD_LIMIT", "1")  # we parallelise across regions instead
WORD = re.compile(r"[A-Za-z]{2,}")


def _prep(crop: np.ndarray) -> np.ndarray:
    # D2R text is bright (white/yellow/green/blue/gold) over a darker game scene:
    # keep pixels whose brightest channel is high, black text on white for tesseract.
    m = crop.max(axis=2)
    m = cv2.resize(m, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)  # 720p chat glyphs are ~10 px
    _, bw = cv2.threshold(m, 165, 255, cv2.THRESH_BINARY)
    return 255 - bw


def ocr_text(crop: np.ndarray) -> str:
    img = _prep(crop)
    ink = (img < 128).mean()
    if ink < 0.003 or ink > 0.25:  # nothing bright enough to be text / bright scenery, not text
        return ""
    d = pytesseract.image_to_data(img, config="--oem 1 --psm 6", output_type=pytesseract.Output.DICT,
                                  timeout=2)
    lines: dict[tuple, list[str]] = {}
    for i, w in enumerate(d["text"]):
        w = w.strip()
        if not w or float(d["conf"][i]) < 60:
            continue
        lines.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(w)
    keep = []
    for words in lines.values():
        line = " ".join(words)
        letters = sum(c.isalpha() for c in line)
        if len(WORD.findall(line)) >= 1 and letters >= 4 and letters / max(1, len(line.replace(" ", ""))) >= 0.6:
            keep.append(line)
    return " | ".join(keep)


def ocr_regions(crops: dict) -> dict:
    out = {}
    for k, c in crops.items():
        try:
            t = ocr_text(c)
        except Exception:  # never kill the frame loop over OCR
            t = ""
        if t:
            out[k] = t
    return out


NUM_RE = re.compile(r"(\d{1,5})\s*/\s*(\d{1,5})")


def read_orb_numbers(crop: np.ndarray) -> tuple[int, int] | None:
    """'Life: 1551 / 7679' / 'Mana: 582 / 852' printed above the orbs (when the game shows them)
    -> (current, maximum), or None. Used to calibrate and verify the orb readings.
    Two thresholds are tried (fixed, then Otsu) because the text colour differs between setups."""
    if crop.size == 0:
        return None
    g = cv2.resize(crop.max(axis=2), None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    candidates = [cv2.threshold(g, 150, 255, cv2.THRESH_BINARY)[1],
                  cv2.threshold(g, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]]
    for bw in candidates:
        try:
            txt = pytesseract.image_to_string(
                255 - bw, config="--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789/:LifeMana ", timeout=3)
        except Exception:
            return None
        m = NUM_RE.search(txt)
        if m:
            cur, mx = int(m.group(1)), int(m.group(2))
            if 0 <= cur <= mx and mx > 0:
                return cur, mx
    return None
