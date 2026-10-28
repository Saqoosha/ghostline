"""Which pilot is in which cell, from the OSD craft name inside each feed. The broadcast bars are not reliable for
this: in some rounds a pilot's bar sits on a different cell from their video. OCR (macOS Vision) every feed at a few
times and match the words against the round's official entrants (LiveFPV) by similarity.
usage: osd_names.py <recording> <round> <livefpv_laps.json> [times...]   prints {slot: pilot} as JSON"""
import difflib, json, re, subprocess, sys
from collections import Counter, defaultdict
import numpy as np
from PIL import Image
from ocrmac import ocrmac

src, rnd, laps = sys.argv[1], int(sys.argv[2]), sys.argv[3]
times = [float(x) for x in sys.argv[4:]] or [15, 30, 45, 60, 75]
pilots = [p["pilot"] for r in json.load(open(laps)) if r["round"] == rnd for p in r["pilots"]]
key = lambda s: re.sub(r"[^A-Z0-9]", "", s.upper().replace("FPV", ""))
votes = defaultdict(Counter)
for t in times:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", src, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    if len(raw) != 1920 * 1080 * 3:
        continue
    f = np.frombuffer(raw, np.uint8).reshape(1080, 1920, 3)
    for s in range(8):
        r, c = divmod(s, 3)
        feed = np.ascontiguousarray(np.repeat(np.repeat(f[r * 360 + 42:r * 360 + 360, c * 640 + 80:c * 640 + 560], 3, 0), 3, 1))
        for word, _, _ in ocrmac.OCR(Image.fromarray(feed), recognition_level="accurate").recognize():
            for w in re.split(r"[\s:]+", word):
                k = key(w)
                if len(k) < 3:
                    continue
                best = max(pilots, key=lambda p: difflib.SequenceMatcher(None, k, key(p)).ratio(), default=None)
                if best and difflib.SequenceMatcher(None, k, key(best)).ratio() >= 0.75:
                    votes[s][best] += 1
out = {}
for s, v in sorted(votes.items()):
    p, n = v.most_common(1)[0]
    if n >= 2 and p not in out.values():
        out[s] = p
print(json.dumps({"round": rnd, "entrants": pilots, "slots": out, "votes": {s: dict(v) for s, v in votes.items()}}))
