"""Top view of an SfM model: ground = largest plane of the 3D points (the grass), up = its normal
pointing toward the cameras. Draws the points and each pilot's camera path (images ordered by
the ms timestamp in their file name), and writes paths.json in ground coordinates.
usage: topview.py <model_dir> <out.png>"""
import json, sys
from collections import defaultdict
import numpy as np, pycolmap as p
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

r = p.Reconstruction(sys.argv[1])
P = np.array([pt.xyz for pt in r.points3D.values()])
C = defaultdict(list)
for im in r.images.values():
    pilot, fn = im.name.split("/")
    C[pilot].append((int(fn[:-4]), im.projection_center()))
cams = np.array([c for v in C.values() for _, c in v])

rng = np.random.default_rng(0)
best = (0, None)
for _ in range(2000):
    a, b, c = P[rng.choice(len(P), 3, replace=False)]
    n = np.cross(b - a, c - a)
    if np.linalg.norm(n) < 1e-9:
        continue
    n /= np.linalg.norm(n)
    tol = 0.01 * np.ptp(cams, 0).max()
    k = (np.abs((P - a) @ n) < tol).sum()
    if k > best[0]:
        best = (k, (n, a))
n, a = best[1]
if ((cams - a) @ n).mean() < 0:
    n = -n
ex = np.cross(n, [1, 0, 0]); ex = ex if np.linalg.norm(ex) > .1 else np.cross(n, [0, 1, 0]); ex /= np.linalg.norm(ex)
ey = np.cross(n, ex)
to2 = lambda X: np.c_[(X - a) @ ex, (X - a) @ ey, (X - a) @ n]
print("points", len(P), "ground inliers", best[0], "images", len(r.images))

fig, ax = plt.subplots(figsize=(12, 12))
G = to2(P)
keep = (G[:, 2] > -0.05 * np.ptp(G[:, :2])) & (np.abs(G[:, :2] - np.median(G[:, :2], 0)).max(1) < 3 * np.ptp(to2(cams)[:, :2]))
ax.scatter(G[keep, 0], G[keep, 1], s=0.3, c="0.6")
out = {}
for i, (pilot, v) in enumerate(sorted(C.items())):
    v.sort(key=lambda x: x[0])
    X = to2(np.array([c for _, c in v]))
    ax.plot(X[:, 0], X[:, 1], "-o", ms=2, lw=0.8, label=f"{pilot} ({len(v)})")
    out[pilot] = [[t] + x.round(3).tolist() for (t, _), x in zip(v, X)]
    print(pilot, len(v), "height p50", round(float(np.median(X[:, 2])), 2))
ax.set_aspect("equal"); ax.legend()
fig.savefig(sys.argv[2], dpi=100, bbox_inches="tight")
json.dump(out, open(sys.argv[2].rsplit(".", 1)[0] + ".json", "w"))
json.dump({"origin": a.tolist(), "ex": ex.tolist(), "ey": ey.tolist(), "up": n.tolist()},
          open(sys.argv[2].rsplit(".", 1)[0] + "_frame.json", "w"))
