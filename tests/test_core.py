"""Fast offline checks of the generic core (no network, no video files):  python tests/test_core.py"""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ytdigest import events as E, keyframes, ocr, profiles  # noqa: E402
from ytdigest.prepare import snap_fps  # noqa: E402


def test_profiles():
    assert profiles.names() == ["d2r", "generic"], profiles.names()      # _template is not registered
    assert profiles.analysis_fps(60, profiles.get("generic")) == 60.0
    assert profiles.analysis_fps(120, profiles.get("generic")) == 60.0   # capped
    assert profiles.analysis_fps(30, profiles.get("d2r")) == 25.0        # the profile's own rate
    assert profiles.analysis_fps(59.94, profiles.get("generic")) == 59.94


def test_snap_fps():
    assert abs(snap_fps(29.97) - 30000 / 1001) < 1e-9 and snap_fps(30.0) == 30.0 and snap_fps(120) == 60.0
    assert snap_fps(47.2) == 47.0


def test_ocr_filter():
    assert ocr.good_line("Union bound: Pr[A] + Pr[B]", 80)
    assert ocr.good_line("87%", 90) and not ocr.good_line("87%", 60)
    assert not ocr.good_line("|||||", 95) and not ocr.good_line("a", 99) and not ocr.good_line("xx zz", 90)


def rec(f, fps, **kw):
    return {"f": f, "t": round(f / fps, 3), "motion": kw.pop("motion", 0.05), "bright": kw.pop("bright", 120.0),
            "phash": "0" * 16, **kw}


def test_generic_events():
    fps = 30.0
    recs = [rec(i, fps) for i in range(30 * 60)]
    for i in range(300, 303):  # a cut
        recs[i]["cut"] = True
    for i in range(600, 640):  # black
        recs[i]["bright"] = 3.0
    for i in range(900, 915):  # motion burst
        recs[i]["motion"] = 20.0
    text = [{"t": 10.0, "f": 300, "lines": [{"text": "Hello world slide", "conf": 90}]},
            {"t": 11.0, "f": 330, "lines": [{"text": "Hello world slide", "conf": 90}]},       # repeat: no new event
            {"t": 40.0, "f": 1200, "lines": [{"text": "Second slide title", "conf": 90}]}]
    ev, st = E.derive_generic(recs, fps, text, [])
    kinds = [e["type"] for e in ev]
    assert kinds.count("scene_change") == 1 and kinds.count("black") == 1 and kinds.count("motion_burst") == 1, kinds
    assert kinds.count("text_change") == 2, kinds
    assert all(e["source"] == "generic" for e in ev)
    # a 45 s still picture is one static stretch, but it is split by the text change at 40 s
    statics = [e for e in ev if e["type"] == "static"]
    assert statics and all(e["end"] - e["t"] >= 19 for e in statics), statics


def test_dedupe_text_fuzzy():
    entries = [{"t": 0.0, "f": 0, "lines": [{"text": "Fine ENCHANTED", "conf": 80}]},
               {"t": 1.0, "f": 30, "lines": [{"text": "Fike ENCHANTE", "conf": 80}]},
               {"t": 2.0, "f": 60, "lines": [{"text": "Something else entirely", "conf": 80}]}]
    d = E.dedupe_text(entries)
    assert [x["new"] for x in d] == [["Fine ENCHANTED"], ["Something else entirely"]], d


def test_keyframes():
    cands = [{"t": float(t), "f": t, "path": None} for t in range(0, 120)]
    ph = {t: ("0" * 16 if t < 60 else "f" * 16) for t in range(120)}       # one picture change at 60 s
    kept = keyframes.select(cands, ph, [])
    ts = [k["t"] for k in kept]
    assert ts[0] == 0 and 60 in ts and len(ts) <= 6, ts                    # static run = few tiles (heartbeats)
    assert all(b - a >= keyframes.MIN_GAP for a, b in zip(ts, ts[1:]))
    # dense-event budget: rare types survive, frequent ones are thinned
    ev = [{"t": float(i), "f": i, "type": "teleport"} for i in range(500)] + [{"t": 5.5, "f": 5, "type": "death"}]
    d = keyframes.dense_events(ev, {"teleport", "death"}, 3600)
    assert any(e["type"] == "death" for e in d) and len(d) <= keyframes.DENSE_MAX_PER_HOUR


def test_d2r_profile_needs_calibration():
    p = profiles.get("d2r")
    assert p.fps == 25.0 and p.generic_ocr is False and "teleport" in p.dense_types


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok ", name)
    print(f"{n} tests passed")
