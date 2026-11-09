"""Per-video configuration: which profile a video uses and that profile's calibration.

config/videos/<video_id>.json:
  {"profile": "d2r", "video_id": "...", "hash_mask": {...} | null, "pip_checked": true, "config": {...profile-specific...}}
"""
from __future__ import annotations

from pathlib import Path

from .common import load_json, save_json, video_config_path


def load(video_id: str) -> dict | None:
    return load_json(video_config_path(video_id))


def save(video_id: str, profile: str, cfg: dict | None, **extra) -> Path:
    """extra: generic per-video settings, e.g. hash_mask (corner region to ignore) and pip_checked."""
    p = video_config_path(video_id)
    save_json(p, {"profile": profile, "video_id": video_id, **extra, "config": cfg or {}})
    return p
