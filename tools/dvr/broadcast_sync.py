"""Where a broadcast's seconds fall on the wall clock, from the venue's recordings: the motion of the four FPV cells (the mean
change between frames) in both, cross-correlated minute by minute. A YouTube archive drops the minutes its encoder was cut off,
so its 0 s is not one wall time: JDL R7 10/25's qualifying stream was 08:54:16 until 10:2x, then 08:55:13, then 09:00:01 and
later more (the clock burnt into its top left agrees). Prints, and writes as json, the stretches of one offset:
[{"from": <broadcast s>, "to": <s>, "t0": <wall ms of the broadcast's 0 s over it>}, ...] (broadcast_audio.py's map).
The broadcast (JDL R7) shows the four cells along its top at 1/4 of the width each, 96/1080 down, 261/1080 tall; the recordings
are rt_rec.py's 2x2 (their json: start, fps), the picture the top 348 of 360 lines of a cell.
  python broadcast_sync.py <broadcast.mp4> <guess: wall s of its 0 s, within SPAN> <out.json> <recording.mp4> ...
  env SPAN (600 s), CHUNK (60 s), MINCC (0.5: a minute below it is left out), CACHE (the motion per file, "."), POINTS (1: each minute too)"""
import json, os, sys, cv2, numpy as np
bc, guess, out, recs = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4:]
SPAN, CHUNK, MINCC = float(os.environ.get("SPAN", 600)), float(os.environ.get("CHUNK", 60)), float(os.environ.get("MINCC", 0.5))
CACHE = os.environ.get("CACHE", ".")

def motion(path, cells):                             # per frame, per cell: mean abs change of a 64x36 grey picture; cached
    cache = os.path.join(CACHE, os.path.basename(path) + ".motion.npy")
    if os.path.exists(cache): return np.load(cache)
    cap = cv2.VideoCapture(path); out, prev = [], None
    while True:
        ok, f = cap.read()
        if not ok: break
        g = np.stack([cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32) for c in cells(f)])
        out.append(np.zeros(4) if prev is None else np.abs(g - prev).mean(axis=(1, 2))); prev = g
    m = np.array(out, np.float32); np.save(cache, m); return m
def rcells(f):
    h, w = f.shape[:2]; return [f[k // 2 * h // 2:k // 2 * h // 2 + h // 2 * 348 // 360, k % 2 * w // 2:(k % 2 + 1) * w // 2] for k in range(4)]
def bcells(f):
    h, w = f.shape[:2]; y = h * 96 // 1080; return [f[y:y + h * 261 // 1080, k * w // 4:(k + 1) * w // 4] for k in range(4)]

B = motion(bc, bcells); fb = cv2.VideoCapture(bc).get(cv2.CAP_PROP_FPS)
z = lambda v: (v - v.mean(0)) / (v.std(0) + 1e-6)
pts = []                                             # (broadcast s, wall s of its 0 s, correlation)
for r in recs:
    meta = json.load(open(r[:-4] + ".json")); r0 = meta["start"] / 1000 if meta["start"] > 1e11 else meta["start"]
    R = motion(r, rcells); fr = meta.get("fps", 30)
    tr = np.arange(int(len(R) / fr * fb)) / fb      # onto the broadcast's frame times (29.97 against 30: 7 frames in 4 minutes)
    R = np.stack([np.interp(tr, np.arange(len(R)) / fr, R[:, k]) for k in range(4)], 1)
    n = int(CHUNK * fb)
    for s in range(0, len(R) - n + 1, n):
        a = R[s:s + n]; live = a.std(0) > 0.5        # cells with a moving picture in this minute
        if live.sum() < 1: continue
        mid = r0 + (s + n / 2) / fb; g = guess if not pts else pts[-1][1]   # the last offset found: the next minute is near it
        lo = int((r0 + s / fb - g - SPAN) * fb); hi = int((r0 + s / fb - g + SPAN) * fb) + n
        lo, hi = max(0, lo), min(len(B), hi)
        if hi - lo <= n: continue
        b = B[lo:hi][:, live]; a = z(a[:, live])
        win = np.lib.stride_tricks.sliding_window_view(b, n, axis=0)        # (positions, cells, n)
        mu, sd = win.mean(-1), win.std(-1) + 1e-6
        cc = (np.einsum("pcn,nc->pc", win, a) / n - mu * a.mean(0)) / sd   # Pearson per cell (a is z-scored)
        cc = cc.mean(1); k = int(cc.argmax())
        if cc[k] < MINCC: continue
        tb = (lo + k) / fb; pts.append((tb, r0 + s / fb - tb, float(cc[k])))
pts.sort()
if os.environ.get("POINTS"):
    for tb, t0, c in pts: print(f"  {tb:7.0f} s  0 s = {t0:.3f}  cc {c:.2f}")
segs = []                                            # stretches of one offset: a jump over 0.3 s starts a new one
for tb, t0, c in pts:
    if segs and abs(t0 - np.median(segs[-1]["t0s"])) < 0.3: segs[-1]["t0s"].append(t0); segs[-1]["to"] = tb + CHUNK
    else: segs.append(dict(fr=tb, to=tb + CHUNK, t0s=[t0]))
res = [{"from": round(s["fr"], 1), "to": round(s["to"], 1), "t0": round(float(np.median(s["t0s"])) * 1000), "n": len(s["t0s"])} for s in segs]
for s in res: print(f"{s['from']:7.0f}-{s['to']:7.0f} s  0 s = {s['t0']} ms  ({s['n']} minutes)")
json.dump(res, open(out, "w"), indent=1)
