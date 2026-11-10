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
import re
import sys
from pathlib import Path

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
    # show the lines leading up to the last ##[error]; steps that run afterwards (cleanup,
    # tunnel shutdown) would otherwise fill the tail with noise
    err = max((i for i, l in enumerate(all_lines) if "##[error]" in l), default=None)   # last: a retry loop errors per attempt
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


UPLOAD_HINT = ("📱 **Download failed - upload the file from your phone and comment `/ingest <tag>`.** "
               "Download the video with Seal or NewPipe (720p MP4), then in this repo open *Releases → Draft a new release*, "
               "type a tag (for example `my-video-1`), attach the file (up to 2 GB per file; split longer videos), "
               "*Publish release*, and comment `/ingest my-video-1`. Step by step in the README.")


def explain(tail: str) -> str:
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
        # args: vid sha status workdir   -- the profile wrote <workdir>/calibration_comment.md
        vid, sha, status, work = a[0], a[1], a[2], Path(a[3])
        from . import videoconfig
        prof = (videoconfig.load(vid) or {}).get("profile", "generic")
        img = blob(sha, f"digests/{vid}/calibration.png", raw=True)
        txt = (work / "calibration_comment.md").read_text() if (work / "calibration_comment.md").exists() else ""
        gh.comment(issue, f"🎯 **Calibration for `{vid}`** (profile `{prof}`, {status})\n\n![calibration]({img})\n\n{txt}\n\n"
                          f"Config file: `config/videos/{vid}.json`.")
    elif what == "suggestion":
        work = Path(a[0])
        txt = (work / "suggestion.md").read_text().strip() if (work / "suggestion.md").exists() else ""
        if txt:
            gh.comment(issue, txt)
    elif what == "done":
        vid, sha = a[0], a[1]
        s = load_json(digest_dir(vid) / "summary.json", {})
        counts = ", ".join(f"{k} {v}" for k, v in sorted(s.get("event_counts", {}).items())) or "none"
        rt = s.get("runtime_s", 0)
        slug = os.environ["GITHUB_REPOSITORY"]
        base = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        body = (f"✅ **Digest ready**: [`digests/{vid}/`]({blob(sha, f'digests/{vid}')})\n\n"
                f"- Video: {s.get('title')} ({hms(s.get('duration_s', 0))}), profile `{s.get('profile')}`\n"
                f"- Frames analysed at {s.get('fps'):g} fps (video is {s.get('native_fps'):g} fps): {s.get('frames')}\n"
                f"- Events: **{s.get('events')}** ({counts})\n"
                f"- Contact sheets: {s.get('sheets')} in [Release `sheets-{vid}`]({base}/{slug}/releases/tag/sheets-{vid}) "
                f"({(s.get('sheet_bytes_total') or 0) / 1e6:.0f} MB, not in git) · digest in git: {(s.get('git_bytes') or 0) / 1e6:.1f} MB\n"
                f"- On-screen text entries: {s.get('ocr_entries')}\n"
                f"- Runtime: {rt // 60} min {rt % 60} s\n\n"
                f"Start with [index.md]({blob(sha, f'digests/{vid}/index.md')}) (also listed in [INDEX.md]({blob(sha, 'INDEX.md')})). "
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
                    "Re-run with `/digest`; blocks are often temporary.\n\n" + UPLOAD_HINT + "\n\n")
        else:
            why = explain(tail)
            body = f"❌ **{desc} failed.** [Workflow run]({run_url()})\n\n" + (f"**Reason:** {why}\n\n" if why else "")
            if re.search(r"download|youtube|yt-dlp|403|sign in to confirm", (desc + " " + tail).lower()) and "ingest" not in desc.lower():
                body += UPLOAD_HINT + "\n\n"
        body += tail
        gh.comment(issue, body)
    elif what == "error":
        gh.comment(issue, f"⚠️ {a[0]}")


if __name__ == "__main__":
    main()
