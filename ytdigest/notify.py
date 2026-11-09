"""Issue comments from the workflows.

  python -m ytdigest.notify started  <issue> <video_id> <what>
  python -m ytdigest.notify calibration <issue> <video_id> <sha> <status>
  python -m ytdigest.notify done <issue> <video_id> <sha>
  python -m ytdigest.notify zoom <issue> <video_id> <path> <frames> <sheets> <sha>
  python -m ytdigest.notify failed <issue> <what> [blocked]
  python -m ytdigest.notify error <issue> <message>
"""
from __future__ import annotations

import os
import sys

from .common import GH, digest_dir, hms, load_json, log


def run_url():
    return (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}")


def blob(sha, path, raw=False):
    base = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ['GITHUB_REPOSITORY']}"
    return f"{base}/{'raw' if raw else 'tree' if '.' not in path.rsplit('/', 1)[-1] else 'blob'}/{sha}/{path}"


def log_tail(gh: GH, lines=60) -> str:
    """Last lines of the first failed job in this run."""
    r = gh._req("GET", f"{gh.api}/repos/{gh.repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}/jobs",
                params={"per_page": 100})
    jobs = r.json().get("jobs", []) if r.ok else []
    failed = [j for j in jobs if j.get("conclusion") in ("failure", "timed_out", "cancelled")]
    if not failed:
        return "(no failed job log found)"
    j = failed[0]
    lr = gh._req("GET", f"{gh.api}/repos/{gh.repo}/actions/jobs/{j['id']}/logs")
    if not lr.ok:
        return f"(could not fetch log of job {j['name']}: HTTP {lr.status_code})"
    lr.encoding = "utf-8"
    all_lines = lr.text.strip().splitlines()
    # show the lines leading up to the first ##[error]; steps that run afterwards (cleanup,
    # tunnel shutdown) would otherwise fill the tail with noise
    err = next((i for i, l in enumerate(all_lines) if "##[error]" in l), None)
    if err is None:
        err = next((i for i, l in enumerate(all_lines) if "Post job cleanup." in l), len(all_lines) - 1)
    tail = all_lines[max(0, err + 1 - lines):err + 1]
    # drop the ISO timestamp prefix GitHub adds to every line
    tail = [l.split(" ", 1)[1] if l[:4].isdigit() and " " in l else l for l in tail]
    return f"Job **{j['name']}** failed. Last {len(tail)} log lines:\n\n```\n" + "\n".join(tail)[-6000:] + "\n```"


# yt-dlp failure classes (idea from watch-for-me's error mapping): a plain-words reason for the comment
REASONS = [
    ("private video|this video is private", "The video is private; only its owner's account can watch it."),
    ("members[- ]only|members of this channel", "Members-only video; it cannot be fetched without a login."),
    ("not available in your country|geo[- ]?restrict|blocked it in your country",
     "The video is geo-blocked for the runner's country."),
    ("video unavailable|has been removed|no longer available|has been terminated|does not exist|http error 404",
     "The video is unavailable or was removed."),
    ("confirm your age|age[- ]restricted", "Age-restricted video; it cannot be fetched without a login."),
    ("live stream|premiere", "The video is a live stream / upcoming premiere; try again once it has ended."),
    ("sign in to confirm|http error 403|http error 429",
     "YouTube refused the download (bot check / 403 / 429) on every tunnel attempt."),
    ("no space left", "The runner ran out of disk space."),
]


def explain(tail: str) -> str:
    import re
    low = tail.lower()
    for pat, msg in REASONS:
        if re.search(pat, low):
            return msg
    return ""


def main():
    what, issue = sys.argv[1], sys.argv[2]
    if not issue:
        log("no issue number; skipping comment")
        return
    gh = GH()
    a = sys.argv[3:]
    if what == "started":
        vid, desc = a[0], a[1]
        gh.comment(issue, f"⏳ {desc} for `{vid}` started. [Workflow run]({run_url()})")
    elif what == "calibration":
        from . import hud
        vid, sha, status = a[0], a[1], a[2]
        cfg = hud.load(vid) or {}
        img = blob(sha, f"digests/{vid}/calibration.png", raw=True)
        notes = cfg.get("notes", {})
        body = (f"🎯 **HUD calibration for `{vid}`** ({status}; {notes.get('method', '')})\n\n"
                f"![calibration]({img})\n\n"
                f"Red ellipse = life orb, blue = mana orb, yellow = skill bar, other boxes = OCR text areas. "
                f"The white line in each orb is the measured fill level.\n\n{hud.rect_summary(cfg)}\n\n"
                f"Analysis continues with these regions. If they are wrong, reply with e.g.\n"
                f"`/hud {vid} life=5,79,11,20 mana=84,79,11,20` (x,y,w,h in % of the frame) "
                f"or `/recalibrate {vid}`; the analysis then re-runs from the archived video "
                f"(no re-download). Config file: `config/hud/{vid}.json`.")
        gh.comment(issue, body)
    elif what == "done":
        vid, sha = a[0], a[1]
        s = load_json(digest_dir(vid) / "summary.json", {})
        counts = ", ".join(f"{k} {v}" for k, v in sorted(s.get("event_counts", {}).items())) or "none"
        st = s.get("stats", {})
        rt = s.get("runtime_s", 0)
        body = (f"✅ **Digest ready**: [`digests/{vid}/`]({blob(sha, f'digests/{vid}')})\n\n"
                f"- Video: {s.get('title')} ({hms(s.get('duration_s', 0))})\n"
                f"- Frames analysed at 25 fps: {s.get('frames')} ({st.get('game_frames')} with HUD visible)\n"
                f"- Events: **{s.get('events')}** ({counts})\n"
                f"- Contact sheets: {s.get('sheets')} · transcript lines: {s.get('transcript_lines')} "
                f"({s.get('transcript_source')})\n"
                f"- Runtime: {rt // 60} min {rt % 60} s\n\n"
                f"Start with [index.md]({blob(sha, f'digests/{vid}/index.md')}). "
                f"Zoom: `/zoom {vid} H:MM:SS-H:MM:SS [fps]`. [Run log]({run_url()})")
        gh.comment(issue, body)
    elif what == "zoom":
        vid, path, frames, sheets, sha = a
        rel = os.environ.get("ZOOM_RELEASE", "")
        gh.comment(issue, f"🔍 **Zoom ready**: {frames} frames in {sheets} sheets (2x2, full 720p).\n\n"
                          f"- Sheets (Release assets): {rel}\n"
                          f"- Index with direct sheet links: [`{path}/index.md`]({blob(sha, path + '/index.md')})")
    elif what == "failed":
        desc = a[0]
        tail = log_tail(gh)
        blocked = (len(a) > 1 and a[1] == "true") or "YouTube blocked the runner" in tail
        if blocked:
            body = ("🚫 **YouTube blocked the download** (bot check / HTTP 403), even through the WARP tunnel. "
                    "Re-run with `/digest`; blocks are often temporary. If it keeps failing, see README → "
                    "*YouTube access*.\n\n")
        else:
            why = explain(tail)
            body = f"❌ **{desc} failed.** [Workflow run]({run_url()})\n\n" + (f"**Reason:** {why}\n\n" if why else "")
        body += tail
        gh.comment(issue, body)
    elif what == "error":
        gh.comment(issue, f"⚠️ {a[0]}")


if __name__ == "__main__":
    main()
