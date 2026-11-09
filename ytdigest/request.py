"""Turn the triggering GitHub event into job parameters (step outputs).

Digest triggers
  - issue labeled "digest"            -> URL taken from the issue body (or title)
  - comment "/digest [url]"           -> (re)run; reuses the release archive if present
  - comment "/hud <id> life=x,y,w,h ..."  -> set HUD regions (percent of frame) and re-run analysis
  - comment "/recalibrate <id>"       -> re-detect the HUD and re-run analysis
  - workflow_dispatch (url, issue)
Delete trigger
  - comment "/delete <video_id>"      -> removes digests/<id>, zooms/<id>, config/hud/<id>.json and the Releases
Zoom trigger
  - comment "/zoom <id> H:MM:SS-H:MM:SS [fps]"
  - workflow_dispatch (video_id, range, fps, issue)
"""
from __future__ import annotations

import json
import os
import re
import sys

from .common import gh_output, parse_video_id

TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
URL_RE = re.compile(r"https?://\S+")


def find_target(*texts: str) -> str | None:
    for t in texts:
        for u in URL_RE.findall(t or ""):
            if parse_video_id(u):
                return u.rstrip(").,>]")
    for t in texts:
        if t and parse_video_id(t.strip()):
            return t.strip()
    return None


def main():
    kind = sys.argv[1]  # digest | zoom
    ev = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
    name = os.environ["GITHUB_EVENT_NAME"]
    out = {"run": "false", "issue": "", "target": "", "mode": "", "sets": "", "force": "false"}
    issue = ev.get("issue") or {}
    out["issue"] = str(issue.get("number", ""))

    if name == "workflow_dispatch":
        inp = ev.get("inputs", {})
        out["issue"] = inp.get("issue", "") or ""
        if kind == "delete":
            out.update(run="true", target=inp.get("video_id", ""), mode="delete")
        elif kind == "digest":
            out.update(run="true", target=inp.get("url", ""), mode="dispatch",
                       force="true" if inp.get("recalibrate") in (True, "true") else "false")
        else:
            out.update(run="true", target=inp.get("video_id", ""), range=inp.get("range", ""),
                       fps=str(inp.get("fps") or "25"), mode="dispatch")
    elif name == "issues" and kind == "digest":
        if (ev.get("label") or {}).get("name") == "digest":
            tgt = find_target(issue.get("body"), issue.get("title"))
            out.update(run="true" if tgt else "false", target=tgt or "", mode="label")
            if not tgt:
                out["error"] = "No YouTube URL found in the issue body or title."
    elif name == "issue_comment":
        c = ev["comment"]
        if c.get("author_association") not in TRUSTED or c.get("user", {}).get("type") == "Bot":
            gh_output(**out)
            return
        line = (c.get("body") or "").strip().splitlines()[0] if (c.get("body") or "").strip() else ""
        words = line.split()
        cmd = words[0].lower() if words else ""
        if kind == "digest" and cmd in ("/digest", "/hud", "/recalibrate"):
            arg = words[1] if len(words) > 1 else ""
            tgt = find_target(arg, issue.get("body"), issue.get("title"))
            out.update(run="true" if tgt else "false", target=tgt or "", mode=cmd[1:])
            if cmd == "/hud":
                sets = [w for w in words[2:] if "=" in w]
                out["sets"] = " ".join(sets)
                if not sets:
                    out.update(run="false", error="Usage: /hud <video_id> life=x,y,w,h mana=x,y,w,h "
                               "[skills=..] [chat=..] [center=..] [top_right=..] [full_life=0.9] (percent of frame)")
            if cmd == "/recalibrate":
                out["force"] = "true"
            if not tgt:
                out["error"] = "No YouTube URL / video id found."
        elif kind == "delete" and cmd == "/delete":
            if len(words) < 2:
                out["error"] = "Usage: /delete <video_id>"
            else:
                out.update(run="true", target=words[1], mode="delete")
        elif kind == "zoom" and cmd == "/zoom":
            if len(words) < 3:
                out["error"] = "Usage: /zoom <video_id> H:MM:SS-H:MM:SS [fps]"
            else:
                out.update(run="true", target=words[1], range=words[2],
                           fps=words[3] if len(words) > 3 else "25", mode="zoom")
    vid = parse_video_id(out.get("target", "")) or ""
    out["video_id"] = vid
    if out["run"] == "true" and not vid:
        out.update(run="false", error=f"Could not read a YouTube video id from {out.get('target')!r}")
    if out.get("range") and not re.fullmatch(r"[0-9:.]+-[0-9:.]+", out["range"]):
        out.update(run="false", error=f"Bad range {out['range']!r}; use H:MM:SS-H:MM:SS")
    if out.get("fps") and not re.fullmatch(r"[0-9.]+", out["fps"]):
        out.update(run="false", error=f"Bad fps {out['fps']!r}")
    gh_output(**out)


if __name__ == "__main__":
    main()
