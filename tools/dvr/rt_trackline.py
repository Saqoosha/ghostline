"""The course line from live answers alone (rt_track.py .jsonl, any cells, any heats), when there is no race spec: no gate
and no lap times. As make_race.py's line, the laps averaged by DTW, but the laps are found from the paths themselves.
  rt_trackline.py out.json a.jsonl [b.jsonl ...]     env: MIN_INL (80), GAP (0.5 s), MIN_LEN (30 s), NEAR (2.5 m)
- Flights: answers with MIN_INL inliers or more, split where no answer came for GAP s; those of MIN_LEN s or more, at 30 Hz.
- Laps: a point of the longest flight that it passes again and again in the same direction (within NEAR m, heading within
  45 deg) cuts every flight there; a lap is one pass to the next. Laps far from the median duration or length are dropped.
- Line: make_race.py's - laps resampled, DTW-matched to the mean, averaged, 0.5 m smoothing, three rounds, 1,600 points.
out.json is what rt_control.py reads as a course line ({"track": [[x, y, z], ...]})."""
import json, os, sys, numpy as np

# make_race.py's (it runs on import, so they are copied)
def resample(pts, m):
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]; u = np.linspace(0, s[-1], m)
    return np.c_[[np.interp(u, s, pts[:, d]) for d in range(3)]].T
def dtw(A, B, band):
    n_, m = len(A), len(B); D = np.full((n_ + 1, m + 1), np.inf); D[0, 0] = 0
    for i in range(1, n_ + 1):
        cc = int(i * m / n_); lo = max(1, cc - band); hi = min(m, cc + band); d = np.linalg.norm(B[lo - 1:hi] - A[i - 1], axis=1)
        for jj, j in enumerate(range(lo, hi + 1)): D[i, j] = d[jj] + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    i, j = n_, m; pairs = []
    while i > 0 and j > 0:
        pairs.append((i - 1, j - 1)); k_ = np.argmin([D[i - 1, j - 1], D[i - 1, j], D[i, j - 1]])
        if k_ == 0: i, j = i - 1, j - 1
        elif k_ == 1: i -= 1
        else: j -= 1
    return pairs[::-1]
def smooth(x, sig):
    r = int(3 * sig); ww = np.exp(-0.5 * (np.arange(-r, r + 1) / sig) ** 2); ww /= ww.sum()
    xp = np.r_[np.repeat(x[:1], r, 0), x, np.repeat(x[-1:], r, 0)]
    return np.c_[[np.convolve(xp[:, d], ww, 'valid') for d in range(3)]].T

E = lambda k, d: float(os.environ.get(k, d))
MIN_INL, GAP, MIN_LEN, NEAR, FPS = E("MIN_INL", 80), E("GAP", 0.5), E("MIN_LEN", 30), E("NEAR", 2.5), 30.0
out, files = sys.argv[1], sys.argv[2:]
flights = []
for f in files:
    rows = sorted((r["done"], r["pos"]) for r in map(json.loads, open(f)) if "pos" in r and r.get("inl", 0) >= MIN_INL)
    if not rows: continue
    t = np.array([r[0] for r in rows]); P = np.array([r[1] for r in rows]); cut = np.where(np.diff(t) > GAP)[0] + 1
    for a, b in zip(np.r_[0, cut], np.r_[cut, len(t)]):
        if t[b - 1] - t[a] < MIN_LEN: continue
        tt = np.arange(t[a], t[b - 1], 1 / FPS); Q = np.c_[[np.interp(tt, t[a:b], P[a:b, d]) for d in range(3)]].T
        flights.append(smooth(Q, 3))                # 0.1 s
print(f"{len(flights)} flights of {MIN_LEN:g} s or more, {sum(len(q) for q in flights) / FPS:.0f} s in all")
if not flights: sys.exit("no flight long enough")

def heading(Q, i): d = Q[min(i + 3, len(Q) - 1), [0, 2]] - Q[max(i - 3, 0), [0, 2]]; n = np.linalg.norm(d); return d / n if n > 1e-6 else d
def passes(Q, ref, h):                              # the frames where Q goes through ref heading h: local minima of the distance
    d = np.linalg.norm(Q[:, [0, 2]] - ref[[0, 2]], axis=1); out_ = []
    for i in np.where(d < NEAR)[0]:
        if d[i] == d[max(i - 15, 0):i + 16].min() and heading(Q, i) @ h > 0.7 and (not out_ or i - out_[-1] > 5 * FPS): out_.append(i)
    return out_
longest = max(flights, key=len); best = None
for i in range(0, len(longest), int(FPS)):          # try a reference point every second of the longest flight
    ref, h = longest[i], heading(longest, i); ps = passes(longest, ref, h)
    if len(ps) < 3: continue
    dur = np.diff(ps) / FPS; score = len(ps) - 4 * np.std(dur) / np.mean(dur)   # many passes, evenly spaced
    if best is None or score > best[0]: best = (score, ref, h, dur)
if best is None: sys.exit("no point the longest flight passes three times")
_, ref, h, dur = best
print(f"reference point {np.round(ref, 2)}: the longest flight passes it {len(dur) + 1} times, laps {np.round(dur, 1)} s")
laps = []
for Q in flights:
    ps = passes(Q, ref, h); laps += [Q[a:b + 1] for a, b in zip(ps[:-1], ps[1:])]
T = np.array([len(q) / FPS for q in laps]); Ls = np.array([np.sum(np.linalg.norm(np.diff(q, axis=0), axis=1)) for q in laps])
keep = (np.abs(T / np.median(T) - 1) < 0.35) & (np.abs(Ls / np.median(Ls) - 1) < 0.15)
laps = [resample(q, 3200) for q, k in zip(laps, keep) if k]
print(f"{len(T)} laps, {len(laps)} kept: {np.median(T):.1f} s, {np.median(Ls):.1f} m (median)")
if len(laps) < 2: sys.exit("fewer than two laps to average")
N = 1600; mean = laps[int(np.argsort([np.sum(np.linalg.norm(np.diff(q, axis=0), axis=1)) for q in laps])[len(laps) // 2])][::2].copy()
for it in range(3):
    per = []
    for Lp in laps:
        acc = [[] for _ in range(N)]
        for i, j in dtw(mean, Lp, 320): acc[i].append(Lp[j])
        per.append(np.array([np.mean(x, axis=0) for x in acc]))
    mean = resample(smooth(np.mean(per, axis=0), 2), N)
print(f"track line {np.sum(np.linalg.norm(np.diff(mean, axis=0), axis=1)):.1f} m from {len(laps)} laps")
json.dump(dict(title="course line from live answers", source=files, laps=len(laps), track=np.round(mean, 2).tolist()), open(out, "w"), separators=(",", ":"))
