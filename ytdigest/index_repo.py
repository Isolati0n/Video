"""Regenerate the top-level INDEX.md: every digest with absolute raw links, readable by URL.

  python -m ytdigest.index_repo

Scans digests/<id>/summary.json and zoom releases (zooms/<id>/*/index.md pointers).
"""
from __future__ import annotations

import os
import subprocess

from .common import ROOT, hms, load_json


def repo_base() -> tuple[str, str, str]:
    slug = os.environ.get("GITHUB_REPOSITORY")
    branch = os.environ.get("DEFAULT_BRANCH")
    if not slug or not branch:
        url = subprocess.check_output(["git", "-C", str(ROOT), "config", "--get", "remote.origin.url"], text=True).strip()
        slug = slug or url.removesuffix(".git").split("github.com")[-1].lstrip(":/")
        branch = branch or subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
                                                   text=True).strip()
    return slug, branch, f"https://raw.githubusercontent.com/{slug}/{branch}"


def build() -> str:
    slug, branch, raw = repo_base()
    web = f"https://github.com/{slug}"
    rows = []
    for d in sorted((ROOT / "digests").glob("*/summary.json")):
        vid = d.parent.name
        s = load_json(d, {}) or {}
        base = f"{raw}/digests/{vid}"
        counts = ", ".join(f"{k} {v}" for k, v in sorted((s.get("event_counts") or {}).items()))
        links = " · ".join([
            f"[index]({base}/index.md)", f"[transcript]({base}/transcript.md)", f"[on-screen text]({base}/ocr.md)",
            f"[events]({base}/events.md)", f"[frames]({base}/frames.jsonl.gz)",
            f"[thumbs]({base}/thumbs/overview_001.jpg)", f"[video]({web}/releases/tag/{vid})"]
            + ([f"[calibration]({base}/calibration.png)"] if (d.parent / "calibration.png").exists() else []))
        n = int(s.get("sheets") or 0)
        sheets = (f"{n} sheets: [Release]({web}/releases/tag/sheets-{vid}), files "
                  f"`{web}/releases/download/sheets-{vid}/sheet_NNNN.jpg` for NNNN = 0001-{n:04d}"
                  + (" (900 per release; further ones in `sheets-" + vid + "-2`, ...)" if n > 900 else "")) if n else "-"
        zooms = sorted((ROOT / "zooms" / vid).glob("*/index.md")) if (ROOT / "zooms" / vid).exists() else []
        zl = " · ".join(f"[{z.parent.name}]({raw}/zooms/{vid}/{z.parent.name}/index.md)" for z in zooms) or "-"
        rows.append(f"| `{vid}` | {str(s.get('title', '')).replace('|', '/')} | {s.get('profile', '?')} | {hms(s.get('duration_s', 0))} | "
                    f"{s.get('fps', '?')} | {s.get('events', '')} ({counts}) | {links} | {sheets} | {zl} |")
    head = ["# yt-digest index", "",
            "Every digested video. All links are raw or Release URLs, so they can be fetched directly. "
            f"Videos live in [Releases]({web}/releases) (tag = video id); contact sheets in the `sheets-<id>` Releases "
            "and listed with time ranges in each `index.md`.", "",
            "Commands (comment on an issue): `/digest [url] [profile: x]`, `/ingest <release-tag | file-url>`, "
            "`/zoom <video_id> H:MM:SS-H:MM:SS [fps]`, `/delete <video_id>`.", "",
            "| video | title | profile | length | fps | events | files | sheets | zooms |", "|---|---|---|---|---|---|---|---|---|"]
    return "\n".join(head + (rows or ["| _none yet_ | | | | | | | | |"])) + "\n"


def main():
    (ROOT / "INDEX.md").write_text(build())


if __name__ == "__main__":
    main()
