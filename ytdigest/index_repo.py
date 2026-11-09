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
            f"[index]({base}/index.md)", f"[transcript]({base}/transcript.md)", f"[events]({base}/events.md)",
            f"[frames]({base}/frames.jsonl.gz)", f"[calibration]({base}/calibration.png)",
            f"[video]({web}/releases/tag/{vid})"])
        zooms = sorted((ROOT / "zooms" / vid).glob("*/index.md")) if (ROOT / "zooms" / vid).exists() else []
        zl = " · ".join(f"[{z.parent.name}]({raw}/zooms/{vid}/{z.parent.name}/index.md)" for z in zooms) or "-"
        rows.append(f"| `{vid}` | {s.get('title', '')} | {hms(s.get('duration_s', 0))} | {s.get('events', '')} "
                    f"({counts}) | {links} | {zl} |")
    head = ["# yt-digest index", "",
            "Every digested video. All links are raw URLs, so they can be fetched directly. "
            f"Videos live in [Releases]({web}/releases) (tag = video id).", "",
            "Ask for a zoom by commenting `/zoom <video_id> H:MM:SS-H:MM:SS [fps]` on the video's issue; "
            "`/delete <video_id>` removes a digest and its Release.", "",
            "| video | title | length | events | files | zooms |", "|---|---|---|---|---|---|"]
    return "\n".join(head + (rows or ["| _none yet_ | | | | | |"])) + "\n"


def main():
    (ROOT / "INDEX.md").write_text(build())


if __name__ == "__main__":
    main()
