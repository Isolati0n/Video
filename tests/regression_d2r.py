"""D2R regression: the d2r profile must reproduce the pre-refactor results.

  python tests/regression_d2r.py <new frames.jsonl.gz> <baseline dir>

The baseline dir holds frames.jsonl.gz and events.json from before profiles existed
(`motion` there is `motion_game` now). Compares every shared per-frame field and the event list.
"""
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ytdigest.profiles import get  # noqa: E402


def load(p):
    return {r["f"]: r for r in (json.loads(l) for l in gzip.open(p, "rt"))}


def main():
    new_p, base = Path(sys.argv[1]), Path(sys.argv[2])
    new, old = load(new_p), load(base / "frames.jsonl.gz")
    print(f"frames: new {len(new)}  baseline {len(old)}  same frame numbers: {set(new) == set(old)}")
    mapping = {"scene": "scene", "hud": "hud", "life": "life", "mana": "mana", "poison": "poison", "shift": "shift",
               "match": "match", "chg": "chg", "cut": "cut", "phash": "phash", "ocr": "ocr", "motion": "motion_game", "t": "t"}
    diffs = {k: 0 for k in mapping}
    first = {}
    for f, o in old.items():
        n = new.get(f)
        if n is None:
            continue
        for ok, nk in mapping.items():
            if o.get(ok) != n.get(nk):
                diffs[ok] += 1
                first.setdefault(ok, (f, o.get(ok), n.get(nk)))
    print("per-field mismatches:", {k: v for k, v in diffs.items() if v} or "none")
    if first:
        print("first mismatches (frame, baseline, new):", first)
    recs = sorted(new.values(), key=lambda r: r["f"])
    ev, stats = get("d2r").derive_events(recs, 25.0, {})
    base_ev = json.load(open(base / "events.json"))
    strip = lambda e: {k: v for k, v in e.items() if k != "source"}
    a, b = [strip(e) for e in ev], [strip(e) for e in base_ev]
    print(f"events: new {len(a)}  baseline {len(b)}  identical lists: {a == b}")
    if a != b:
        sa, sb = {json.dumps(e, sort_keys=True) for e in a}, {json.dumps(e, sort_keys=True) for e in b}
        print("only in new:", list(sa - sb)[:5]); print("only in baseline:", list(sb - sa)[:5])
    from collections import Counter
    print("event counts:", dict(Counter(e["type"] for e in ev)))
    sys.exit(0 if (a == b and not any(diffs.values())) else 1)


if __name__ == "__main__":
    main()
