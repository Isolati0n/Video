"""Tesseract OCR: whole-frame text (the generic core) and small bright-text regions (for profiles)."""
from __future__ import annotations

import os
import re

import cv2
import numpy as np
import pytesseract

os.environ.setdefault("OMP_THREAD_LIMIT", "1")  # we parallelise across regions instead
WORD = re.compile(r"[A-Za-z]{2,}")


def _prep(crop: np.ndarray) -> np.ndarray:
    # Overlay text in games is bright (white/yellow/green/blue/gold) over a darker scene:
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


# ------------------------------------------------------------------------ whole-frame OCR (generic core)
def good_line(text: str, conf: float) -> bool:
    """Keep real-looking text (words, and short numbers such as '87%' or '3:21'); drop OCR noise
    from scenery, textures and interface art."""
    t = text.strip()
    if len(t) < 3 or conf < 60:
        return False
    alnum = sum(c.isalnum() for c in t)
    if alnum / max(1, len(t.replace(" ", ""))) < 0.6:
        return False
    letters = sum(c.isalpha() for c in t)
    digits = sum(c.isdigit() for c in t)
    if letters >= 3:
        # at least one word of 3+ letters, and not one repeated character
        return bool(re.search(r"[A-Za-z]{3,}", t)) and len(set(t.lower().replace(" ", ""))) > 2
    return digits >= 2 and conf >= 80


def ocr_frame(frame: np.ndarray, lang: str = "eng") -> list[dict]:
    """All text lines in one frame as [{'text', 'conf', 'y'}] (sparse-text mode, noise filtered)."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    try:
        d = pytesseract.image_to_data(gray, lang=lang, config="--oem 1 --psm 11",
                                      output_type=pytesseract.Output.DICT, timeout=20)
    except Exception:
        return []
    lines: dict[tuple, list] = {}
    for i, w in enumerate(d["text"]):
        w = (w or "").strip()
        if not w or float(d["conf"][i]) < 40:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        lines.setdefault(key, []).append((w, float(d["conf"][i]), d["top"][i], d["left"][i]))
    fh, fw = frame.shape[:2]
    out = []
    for words in lines.values():
        words.sort(key=lambda x: x[3])
        text = " ".join(w for w, _, _, _ in words)
        conf = sum(c for _, c, _, _ in words) / len(words)
        if good_line(text, conf):
            # x, y = top-left of the line as permille of the frame (0-1000), so positions compare across videos
            out.append({"text": text, "conf": round(conf), "x": int(1000 * words[0][3] / fw),
                        "y": int(1000 * min(t for _, _, t, _ in words) / fh)})
    out.sort(key=lambda x: (x["y"], x["x"]))
    return out


# ------------------------------------------------------------------------ what a reader should look at
LOW_CONF = 72          # a line with average word confidence below this is flagged
MATH_CHARS = set("∑∫≤≥≈≠±√∞∈∂∇⊂⊆∪∩∀∃∧∨→←⇒λμσπθΔΣΩαβγδεφψω^_{}[]<>=~")
MATH_RE = re.compile(r"(Pr\s*\[|E\s*\[|Var\s*\[|\blog\b|\bexp\b|\\frac|\d\s*[=<>]\s*\w|\w\s*[=<>]{1,2}\s*\w+\s*[+\-*/]|[A-Za-z]\(\w+\)\s*[=<>+\-]|\|\s*\w+\s*\|)")


def looks_mathy(text: str) -> bool:
    """Formulas come out of OCR as bracket/operator soup: many operator and bracket characters
    next to a few letters, or recognisable patterns such as Pr[...] and f(x) = ..."""
    t = text.strip()
    if MATH_RE.search(t):
        return True
    sym = sum(1 for c in t if c in MATH_CHARS or c in "+-*/=|\\")
    return len(t) >= 6 and sym >= 3 and sym / len(t) >= 0.15


def classify_text(lines: list[dict]) -> dict:
    """Flags for one frame's OCR lines: ['math', 'dense', 'low_conf'] plus numbers.
    A reader (an AI) should open the image for any flagged keyframe instead of trusting the text."""
    if not lines:
        return {"flags": [], "lines": 0, "chars": 0, "low_conf_share": 0.0}
    n = len(lines)
    chars = sum(len(x["text"]) for x in lines)
    low = sum(1 for x in lines if x.get("conf", 100) < LOW_CONF)
    mathy = sum(1 for x in lines if looks_mathy(x["text"]))
    flags = []
    if mathy >= 1 and (mathy >= 2 or mathy / n >= 0.2):
        flags.append("math")
    if n >= 12 or chars >= 400:
        flags.append("dense")
    if n >= 2 and low / n >= 0.3:
        flags.append("low_conf")
    return {"flags": flags, "lines": n, "chars": chars, "low_conf_share": round(low / n, 2), "math_lines": mathy}
