"""Quick CPU look at a 3DGS PLY: splat centres seen from above, coloured by their DC colour,
opaque ones only, drawn low to high so taller things sit on top.
usage: splat_top.py <in.ply> <out.png> [xmin xmax ymin ymax]"""
import sys
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

raw = open(sys.argv[1], "rb").read()
end = raw.index(b"end_header\n") + len(b"end_header\n")
head = raw[:end].decode()
props = [l.split()[-1] for l in head.splitlines() if l.startswith("property")]
n = int([l for l in head.splitlines() if l.startswith("element vertex")][0].split()[-1])
v = np.frombuffer(raw[end:], np.float32, n * len(props)).reshape(n, len(props))
ix = {p: i for i, p in enumerate(props)}
P = v[:, [ix["x"], ix["y"], ix["z"]]]
op = 1 / (1 + np.exp(-v[:, ix["opacity"]]))
rgb = np.clip(0.5 + 0.2820948 * v[:, [ix["f_dc_0"], ix["f_dc_1"], ix["f_dc_2"]]], 0, 1)
box = [float(x) for x in sys.argv[3:7]] if len(sys.argv) > 6 else [-145, -80, -15, 85]
k = (op > 0.3) & (P[:, 0] > box[0]) & (P[:, 0] < box[1]) & (P[:, 1] > box[2]) & (P[:, 1] < box[3]) & (P[:, 2] < 12)
o = np.argsort(P[k, 2])
fig, ax = plt.subplots(figsize=(8, 12), facecolor="k")
ax.scatter(P[k, 0][o], P[k, 1][o], c=rgb[k][o], s=0.4, linewidths=0)
ax.set_facecolor("k"); ax.set_aspect("equal")
fig.savefig(sys.argv[2], dpi=110, bbox_inches="tight")
print("drawn", int(k.sum()), "of", n)
