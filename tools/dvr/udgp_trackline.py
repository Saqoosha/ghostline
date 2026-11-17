"""UDGP 2026 R3: the course line (track.json, page frame, y up) as one typical real lap, starting at the timing gate.
  uv run --with numpy python tools/dvr/udgp_trackline.py <heats dir: <id>/race.json> <out track.json>
rt_trackline.py averages the laps by DTW; on this two-floor course that collapsed (laps cut where a flight passes the same
spot on both floors, and averaged laps through the floor). Here one real lap is taken instead: the flights cut into laps
where they pass the gate (REF), the laps through both floors of about the median duration, the five of them that stay most
in busy 0.5 m cells (a wrong stretch through a floor or a wall crosses empty ones), then their medoid, smoothed.
2026-11-16, from night2/heats: 667 laps of 18.6 s, the line 181 m, 0.87 m from the other laps on average."""
import glob, json, sys
import numpy as np

src, out = sys.argv[1:3]
REF = np.array([-13.25, 1.0, 10.45])   # the timing gate's middle, read off the floor plan (page frame)
NEAR, MINLAP, FPS, VMAX = 3.0, 15.0, 30.0, 25.0


def smooth(x, sig):
    r = int(3 * sig); w = np.exp(-0.5 * (np.arange(-r, r + 1) / sig) ** 2); w /= w.sum()
    xp = np.r_[np.repeat(x[:1], r, 0), x, np.repeat(x[-1:], r, 0)]
    return np.c_[[np.convolve(xp[:, d], w, 'valid') for d in range(3)]].T


def resample(p, m):
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]; u = np.linspace(0, s[-1], m)
    return np.c_[[np.interp(u, s, p[:, d]) for d in range(3)]].T


laps, ALL = [], []
for f in sorted(glob.glob(src + '/*/race.json')):
    race = json.load(open(f)); fps = race.get('fps', 60)
    for pilot in race['pilots']:
        rows = [(i / fps, q) for i, q in enumerate(pilot['pos']) if q]
        if len(rows) < 3: continue
        t = np.array([r[0] for r in rows]); P = np.array([r[1] for r in rows])
        keep = np.ones(len(t), bool)                  # like udgp_heats.clean(): a lone pose both neighbours need > VMAX to reach
        for _ in range(5):
            i = np.flatnonzero(keep); v = np.linalg.norm(np.diff(P[i], axis=0), axis=1) / np.maximum(np.diff(t[i]), 1e-3)
            bad = np.r_[v[0] > VMAX, (v[:-1] > VMAX) & (v[1:] > VMAX), v[-1] > VMAX]
            if not bad.any(): break
            keep[i[bad]] = False
        t, P = t[keep], P[keep]; ALL.append(P); cut = np.where(np.diff(t) > 0.5)[0] + 1
        for a, b in zip(np.r_[0, cut], np.r_[cut, len(t)]):
            if t[b - 1] - t[a] < 30: continue
            tt = np.arange(t[a], t[b - 1], 1 / FPS); Q = smooth(np.c_[[np.interp(tt, t[a:b], P[a:b, d]) for d in range(3)]].T, 3)
            d = np.linalg.norm(Q - REF, axis=1); ps = []
            for i in np.where(d < NEAR)[0]:
                if d[i] == d[max(i - 15, 0):i + 16].min() and (not ps or i - ps[-1] > MINLAP * FPS): ps.append(i)
            laps += [Q[i:j + 1] for i, j in zip(ps[:-1], ps[1:])]
if not laps: sys.exit('no lap through the gate')
T = np.array([len(q) / FPS for q in laps]); both = np.array([q[:, 1].min() < 2.0 and q[:, 1].max() > 6.0 for q in laps])
if not both.any(): sys.exit('no lap through both floors')
ok = both & (np.abs(T / np.median(T[both]) - 1) < 0.2)
print(f'{len(laps)} laps, {both.sum()} through both floors, median {np.median(T[both]):.1f} s')
A = np.concatenate(ALL); lo, hi = A.min(0), A.max(0); key = lambda p: tuple(((p - lo) / 0.5).astype(int).T)
G = np.zeros(tuple(((hi - lo) / 0.5).astype(int) + 1)); np.add.at(G, key(A), 1)
dens = np.array([np.mean(G[key(np.clip(q, lo, hi))] >= 30) for q in laps])   # the share of the lap in busy cells
keep = ok & (dens >= np.sort(dens[ok])[::-1][min(4, ok.sum() - 1)])
L = np.array([resample(q, 400) for q, k in zip(laps, keep) if k])
cost = np.array([np.mean(np.linalg.norm(L - q, axis=2)) for q in L])
line = resample(smooth(resample(L[np.argmin(cost)], 1600), 6), 1600)
print(f'{len(L)} laps scored, the chosen one {cost.min():.2f} m from the others; line {np.sum(np.linalg.norm(np.diff(line, axis=0), axis=1)):.1f} m')
json.dump({'title': 'course line: one typical lap from the timing gate', 'laps': len(L), 'track': np.round(line, 2).tolist()}, open(out, 'w'), separators=(',', ':'))
