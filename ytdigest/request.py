"""Turn the triggering GitHub event into job parameters (step outputs).

Digest triggers
  - issue labeled "digest"              -> YouTube URL from the issue body (or title); an optional line
                                           "profile: d2r" in the body picks a profile (default: generic)
  - comment "/digest [url] [profile: x]" -> (re)run; reuses the Release archive if present
  - comment "/set <id> key=value ..."    -> set the video's profile calibration values (keys belong to the profile;
                                           "/hud" is an alias) and re-run the analysis
  - comment "/recalibrate <id>"          -> redo the profile's calibration and re-run the analysis
  - comment "/ingest <release-tag | file-url> [as <name>] [profile: x]"
                                         -> digest a file you uploaded to a Release (phone) or a direct file URL
  - workflow_dispatch (url, issue, profile, recalibrate, ingest)
Delete trigger
  - comment "/delete <video_id>"
Zoom trigger
  - comment "/zoom <id> H:MM:SS-H:MM:SS [fps]"   (default: native fps)
  - workflow_dispatch (video_id, range, fps, issue)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

from .common import gh_output, parse_video_id

TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
URL_RE = re.compile(r"https?://\S+")
PROFILE_RE = re.compile(r"(?i)(?:^|\s)[`*_]*profile[`*_]*\s*[:=]\s*[`*_]*([A-Za-z0-9_-]+)", re.M)
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
RESERVED = ("sheets-", "zoom-")


def find_target(*texts: str) -> str | None:
    for t in texts:
        for u in URL_RE.findall(t or ""):
            if parse_video_id(u):
                return u.rstrip(").,>]")
    for t in texts:
        if t and parse_video_id(t.strip()):
            return t.strip()
    return None


def find_profile(*texts: str) -> str:
    for t in texts:
        m = PROFILE_RE.search(t or "")
        if m:
            return m.group(1).lower()
    return ""


def ingest_id(source: str, name: str) -> str:
    if name:
        return name
    if re.match(r"https?://", source, re.I):
        return "url-" + hashlib.sha1(source.encode()).hexdigest()[:8]
    return source


def main():
    from . import profiles
    kind = sys.argv[1]  # digest | zoom | delete
    ev = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
    name = os.environ["GITHUB_EVENT_NAME"]
    out = {"run": "false", "issue": "", "target": "", "mode": "", "sets": "", "force": "false", "profile": "",
           "source": "", "range": "", "fps": ""}
    issue = ev.get("issue") or {}
    out["issue"] = str(issue.get("number", ""))
    body = issue.get("body") or ""

    if name == "workflow_dispatch":
        inp = ev.get("inputs", {})
        out["issue"] = inp.get("issue", "") or ""
        if kind == "delete":
            out.update(run="true", target=inp.get("video_id", ""), mode="delete")
        elif kind == "digest":
            out.update(run="true", target=inp.get("url", ""), mode="dispatch", profile=(inp.get("profile") or "").lower(),
                       force="true" if inp.get("recalibrate") in (True, "true") else "false")
            if inp.get("ingest"):
                out.update(mode="ingest", source=inp["ingest"], target=ingest_id(inp["ingest"], ""))
        else:
            out.update(run="true", target=inp.get("video_id", ""), range=inp.get("range", ""),
                       fps=str(inp.get("fps") or ""), mode="dispatch")
    elif name == "issues" and kind == "digest":
        if (ev.get("label") or {}).get("name") == "digest":
            tgt = find_target(body, issue.get("title"))
            out.update(run="true" if tgt else "false", target=tgt or "", mode="label", profile=find_profile(body))
            if not tgt:
                out["error"] = ("No YouTube URL found in the issue body or title. For a file you uploaded yourself "
                                "comment `/ingest <release-tag>` (see README, phone steps).")
    elif name == "issue_comment":
        c = ev["comment"]
        if c.get("author_association") not in TRUSTED or c.get("user", {}).get("type") == "Bot":
            gh_output(**out)
            return
        text = c.get("body") or ""
        words = (text.strip().splitlines()[0] if text.strip() else "").split()
        cmd = words[0].lower() if words else ""
        prof = find_profile(text, body if cmd in ("/digest", "/hud", "/set", "/recalibrate") else "")
        if kind == "digest" and cmd in ("/digest", "/hud", "/set", "/recalibrate"):
            arg = words[1] if len(words) > 1 else ""
            tgt = find_target(arg, body, issue.get("title"))
            if not tgt and arg and SAFE_ID.fullmatch(arg) and cmd != "/digest":
                tgt = arg   # /set and /recalibrate also accept the id of an ingested file
            out.update(run="true" if tgt else "false", target=tgt or "", mode=cmd[1:], profile=prof)
            if cmd in ("/hud", "/set"):
                sets = [w for w in words[2:] if "=" in w]
                out["sets"] = " ".join(sets)
                if not sets:
                    out.update(run="false", error="Usage: /set <video_id> key=value [key=value ...]. The keys belong to the "
                               "video's profile (see ytdigest/profiles/README.md and the profile's own docs); the generic "
                               "profile has none.")
            if cmd == "/recalibrate":
                out["force"] = "true"
            if not tgt:
                out["error"] = "No YouTube URL / video id found. For an uploaded file use `/ingest <release-tag>`."
        elif kind == "digest" and cmd == "/ingest":
            src = words[1] if len(words) > 1 else ""
            alias = ""
            if "as" in [w.lower() for w in words[2:]]:
                i = [w.lower() for w in words].index("as")
                alias = words[i + 1] if len(words) > i + 1 else ""
            vid = ingest_id(src, alias)
            if not src:
                out["error"] = "Usage: /ingest <release-tag | https://file-url> [as <name>] [profile: x]"
            elif not SAFE_ID.fullmatch(vid) or vid.startswith(RESERVED):
                out["error"] = (f"`{vid}` cannot be used as a name: letters, digits, `_ - .` only (max 80), and it must "
                                "not start with `sheets-` or `zoom-`.")
            else:
                out.update(run="true", target=vid, source=src, mode="ingest", profile=prof)
        elif kind == "delete" and cmd == "/delete":
            if len(words) < 2:
                out["error"] = "Usage: /delete <video_id>"
            else:
                out.update(run="true", target=words[1], mode="delete")
        elif kind == "zoom" and cmd == "/zoom":
            if len(words) < 3:
                out["error"] = "Usage: /zoom <video_id> H:MM:SS-H:MM:SS [fps]"
            else:
                out.update(run="true", target=words[1], range=words[2], fps=words[3] if len(words) > 3 else "", mode="zoom")

    mode = out["mode"]
    t = out.get("target", "")
    vid = (parse_video_id(t) or (t if SAFE_ID.fullmatch(t or "") else "")) if mode != "ingest" else t
    if mode in ("hud", "set", "recalibrate", "zoom", "delete", "digest", "label", "dispatch"):
        vid = parse_video_id(t) or (t if SAFE_ID.fullmatch(t or "") and mode in ("hud", "set", "recalibrate", "zoom", "delete") else "")
    out["video_id"] = vid
    if out["run"] == "true" and not vid:
        out.update(run="false", error=f"Could not read a video id from {t!r}")
    if out.get("range") and not re.fullmatch(r"[0-9:.]+-[0-9:.]+", out["range"]):
        out.update(run="false", error=f"Bad range {out['range']!r}; use H:MM:SS-H:MM:SS")
    if out.get("fps") and not re.fullmatch(r"[0-9.]+", out["fps"]):
        out.update(run="false", error=f"Bad fps {out['fps']!r}")
    if out["profile"] and out["profile"] not in profiles.names():
        out.update(run="false", error=f"Unknown profile `{out['profile']}`. Available: {', '.join(profiles.names())}.")
    gh_output(**out)


if __name__ == "__main__":
    main()
