"""Fast offline checks of the generic core (no network, no video files):  python tests/test_core.py"""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ytdigest import events as E, furniture, keyframes, ocr, profiles  # noqa: E402
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
    ev, st = E.derive_generic(recs, fps, text)
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


def line(text, x, y, conf=90):
    return {"text": text, "conf": conf, "x": x, "y": y}


def test_furniture_suppression():
    # a menu bar that is on screen the whole video, a clock at a fixed place that keeps changing,
    # a slide that stays for half the video, and slide titles that change
    entries = []
    for i in range(100):
        lines = [line("Preview File Edit View Window Help", 50, 10), line(f"Wed May 28 {i}:{i % 60:02d} PM", 800, 10),
                 line(f"Slide title number {i // 10}", 300, 300)]
        if i < 50:
            lines.append(line("A caption that stays for half the video", 300, 600))
        entries.append({"t": float(i), "f": i * 30, "lines": lines})
    kept, furn = furniture.suppress(entries, 100.0)
    gone = {x["text"] for x in furn}
    assert "Preview File Edit View Window Help" in gone, gone
    texts = {ln["text"] for e in kept for ln in e["lines"]}
    assert any(t.startswith("Wed May 28") for t in texts)                 # changing text at a fixed place is content
    assert "A caption that stays for half the video" in texts              # present in 3 of 5 slices only: content
    assert any(t.startswith("Slide title number") for t in texts)


def test_text_keyframes_and_flags():
    ents = []
    for t in range(0, 60):
        title = "Alpha slide about cats" if t < 30 else "Beta slide about dogs"
        ents.append({"t": float(t), "f": t, "lines": [line(title, 100, 120), line("Some body line of text here", 100, 200),
                                                         line(f"Footnote {t % 2} of the page", 100, 900)]})
    kf = keyframes.text_keyframes(ents, set(range(60)), 60)
    assert [k["t"] for k in kf] == [0.0, 30.0], kf                          # one per slide, not per flicker
    mathy = ocr.classify_text([line("Pr[A V B] < Pr[A] + Pr[B].", 0, 0), line("f (E[X]) <E[f(X)] .", 0, 0, 60)])
    assert "math" in mathy["flags"], mathy
    tags = ocr.classify_text([line("[A] | ARMADA", 0, 0), line("[A] | ARMADA IL]", 0, 0), line("FOX MVG | MEW2KING", 0, 0)])
    assert "math" not in tags["flags"], tags                                  # player tags are not formulas
    plain = ocr.classify_text([line("Basic Inequalities", 0, 0), line("Hoeffding Inequality", 0, 0)])
    assert plain["flags"] == [], plain
    assert "dense" in ocr.classify_text([line(f"line {i} of many many words", 0, 0) for i in range(14)])["flags"]


def test_pip_detection():
    import cv2
    import numpy as np
    from ytdigest import pip

    class Arc:
        duration = 120.0

        def frames(self, a, b, fps, size):
            rng = np.random.default_rng(int(a * 10))
            for k in range(int((b - a) * fps)):
                img = np.full((180, 320, 3), 200, np.uint8)               # a still slide ...
                cv2.putText(img, "Slide", (60, 90), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
                img[110:180, 0:100] = rng.integers(0, 255, (70, 100, 3), dtype=np.uint8)   # ... and a moving webcam
                yield a + k / fps, img

    r = pip.detect(Arc())
    assert r and r["rect"][0] <= 0.05 and r["rect"][1] >= 0.4 and r["rect"][2] < 0.5, r

    class Still(Arc):
        def frames(self, a, b, fps, size):
            for k in range(int((b - a) * fps)):
                yield a + k / fps, np.full((180, 320, 3), 120, np.uint8)

    assert pip.detect(Still()) is None


def test_detectors_are_extensible():
    class Fake(profiles.Profile):
        name = "fake"
        def detect(self, frames):
            return 0.8
    profiles._load_all()
    profiles._REGISTRY["fake"] = Fake()
    try:
        found = dict(profiles.suggestions([None] * 5))
        assert found.get("fake") == 0.8, found
        assert "generic" not in found
    finally:
        del profiles._REGISTRY["fake"]


def test_profile_line_parsing():
    from ytdigest.request import find_profile
    assert find_profile("https://youtu.be/x\n\nGeneric profile: what does the output capture?") == ""   # prose
    assert find_profile("https://youtu.be/x\nprofile: d2r") == "d2r"
    assert find_profile("/digest profile: generic") == "generic"
    assert find_profile("/ingest my-rec as x profile: d2r") == "d2r"
    assert find_profile("/digest\n`profile`: Melee") == "melee"


def test_caption_strip_does_not_drive_keyframes():
    # burned-in subtitles (bottom strip) must not drive text keyframes; a changing title above them still does
    ents = []
    for t in range(60):
        ents.append({"t": float(t), "f": t, "lines": [line("Interview with the author", 100, 120),
                                                         line("Lower third: Dr. Jane Smith", 100, 300),
                                                         line(f"This caption changes every second number {t}", 300, 880)]})
    assert len(keyframes.text_keyframes(ents, set(range(60)), 60)) == 1


def test_talking_head_does_not_flood_keyframes():
    cands = [{"t": float(t), "f": t, "path": None} for t in range(0, 120)]
    ph = {t: ("0" * 16 if t % 2 == 0 else "00000000000000ff") for t in range(120)}   # the head moves: an 8-bit change each step
    ti = keyframes.TextIndex([])
    plain = keyframes.select(cands, ph, [])                                    # not slide-like: every change counts
    slide = keyframes.select(cands, ph, [], ti, [], slide_like=True)
    assert len(plain) > 20 and len(slide) <= 6, (len(plain), len(slide))


def test_d2r_profile_needs_calibration():
    p = profiles.get("d2r")
    assert p.fps == 25.0 and p.generic_ocr is False and "teleport" in p.dense_types


def _clip(path, audio: bool, secs=12):
    """A small 640x360 clip: moving block, a text line that changes at 6 s; optional sine audio."""
    import subprocess
    import cv2
    import numpy as np
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "640x360", "-r", "30", "-i", "-"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-shortest", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(secs * 30):
        f = np.full((360, 640, 3), 40, np.uint8)
        cv2.rectangle(f, (20 + i * 3 % 400, 200), (120 + i * 3 % 400, 300), (200, 200, 60), -1)
        cv2.putText(f, "FIRST SLIDE TITLE" if i < 180 else "SECOND SLIDE TITLE", (30, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()


def _streams(path):
    import json
    import subprocess
    out = subprocess.check_output(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)])
    return [s["codec_type"] for s in json.loads(out)["streams"]]


def test_pipeline_completes_with_and_without_audio():
    """Visual data only: the archive never has an audio track, and a source with no audio stream (a phone screen
    recording) or with one goes through archive -> analysis -> finalize the same way."""
    import json
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    from ytdigest import prepare
    root = Path(__file__).resolve().parent.parent
    import shutil
    dg = root / "digests" / "T1"          # the pipeline writes into the repo's digests/; removed again below
    assert not dg.exists()
    for has_audio in (True, False):
        shutil.rmtree(dg, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src = d / "src.mp4"
            _clip(src, has_audio)
            assert ("audio" in _streams(src)) == has_audio
            arch = d / "work" / "archive"
            arch.mkdir(parents=True)
            full = arch / "T1.mp4"
            prepare.encode_archive(src, full)
            assert _streams(full) == ["video"], _streams(full)                        # -an: never an audio track
            parts = prepare.split_archive(full, "T1", arch, 12.0)
            assert all(_streams(arch / x["name"]) == ["video"] for x in parts)
            (d / "work" / "parts.json").write_text(json.dumps({"video_id": "T1", "duration": 12.0, "width": 640, "height": 360,
                                                              "fps": 30.0, "parts": parts}))
            (d / "work" / "info.json").write_text(json.dumps({"title": "t", "id": "T1"}))
            run = lambda *a: subprocess.run([sys.executable, "-m", "ytdigest." + a[0], *a[1:]], cwd=d, check=True,
                                            env={**__import__("os").environ, "PYTHONPATH": str(root)}, capture_output=True, text=True)
            run("analyze", "T1", "0", "12", "chunk", "--workdir", "work", "--archive-dir", str(arch))
            run("finalize", "T1", "chunk", "work", "--archive-dir", str(arch), "--no-upload")
            for name in ("index.md", "events.md", "events.json", "ocr.md", "frames.jsonl.gz", "keyframes.json", "summary.json"):
                assert (dg / name).exists(), (has_audio, name)
            assert not (dg / "transcript.md").exists()
            types = {e["type"] for e in json.load(open(dg / "events.json"))}
            assert not types & {"silence", "loud_peak"}, types
            assert "SLIDE TITLE" in (dg / "ocr.md").read_text().upper()
            assert "transcript" not in (dg / "index.md").read_text().lower()
    shutil.rmtree(dg, ignore_errors=True)


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok ", name)
    print(f"{n} tests passed")
