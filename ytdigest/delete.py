"""Delete a digest's generated Releases and tags: the archive, the contact sheets and every zoom.

  python -m ytdigest.delete <video_id>

If the video's own Release (tag = id) also holds files the OWNER uploaded for /ingest, those are left
alone: only the assets this tool generated are removed and the Release stays.
"""
from __future__ import annotations

import re
import sys

from .common import GH, gh_output, log
from .ingest import OURS

SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")


def main():
    vid = sys.argv[1].strip()
    if not SAFE_ID.fullmatch(vid):
        raise SystemExit(f"not a video id: {vid!r}")
    gh = GH()
    removed, kept, page = [], [], 1
    sheets_re = re.compile(rf"sheets-{re.escape(vid)}(-\d+)?")
    while True:
        r = gh._req("GET", f"{gh.api}/repos/{gh.repo}/releases", params={"per_page": 100, "page": page})
        r.raise_for_status()
        rels = r.json()
        if not rels:
            break
        for rel in rels:
            tag = rel["tag_name"]
            if tag == vid:
                theirs = [a for a in rel.get("assets", []) if not OURS.search(a["name"])]
                if theirs:  # the owner's uploaded video(s): remove only what we generated
                    for a in rel["assets"]:
                        if OURS.search(a["name"]):
                            gh._req("DELETE", a["url"])
                    kept.append(f"{tag} (kept: {len(theirs)} uploaded file(s))")
                    continue
            elif not (tag.startswith(f"zoom-{vid}-") or sheets_re.fullmatch(tag)):
                continue
            gh._req("DELETE", f"{gh.api}/repos/{gh.repo}/releases/{rel['id']}").raise_for_status()
            gh._req("DELETE", f"{gh.api}/repos/{gh.repo}/git/refs/tags/{tag}")  # tag may already be gone
            removed.append(tag)
            log(f"deleted release {tag} ({len(rel.get('assets', []))} assets)")
        page += 1
    gh_output(video_id=vid, releases=",".join(removed + kept), count=str(len(removed) + len(kept)))


if __name__ == "__main__":
    main()
