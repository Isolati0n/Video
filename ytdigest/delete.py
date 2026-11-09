"""Delete a digest's Releases and tags (the video archive and every zoom release).

  python -m ytdigest.delete <video_id>      prints what was removed; the workflow removes the files in git
"""
from __future__ import annotations

import sys

from .common import GH, gh_output, log, parse_video_id


def main():
    vid = parse_video_id(sys.argv[1])
    if not vid:
        raise SystemExit(f"not a video id: {sys.argv[1]!r}")
    gh = GH()
    removed, page = [], 1
    while True:
        r = gh._req("GET", f"{gh.api}/repos/{gh.repo}/releases", params={"per_page": 100, "page": page})
        r.raise_for_status()
        rels = r.json()
        if not rels:
            break
        for rel in rels:
            tag = rel["tag_name"]
            if tag == vid or tag.startswith(f"zoom-{vid}-"):
                gh._req("DELETE", f"{gh.api}/repos/{gh.repo}/releases/{rel['id']}").raise_for_status()
                gh._req("DELETE", f"{gh.api}/repos/{gh.repo}/git/refs/tags/{tag}")  # tag may already be gone
                removed.append(tag)
                log(f"deleted release {tag} ({len(rel.get('assets', []))} assets)")
        page += 1
    gh_output(video_id=vid, releases=",".join(removed), count=str(len(removed)))


if __name__ == "__main__":
    main()
