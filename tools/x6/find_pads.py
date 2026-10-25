"""The start pads (four small coloured boxes on the grass) in the scan: near a guess, the solid splats within 0.6 m of the ground
whose colour is clearly red, blue, yellow or near-black against the grass; each colour's median position. Scene frame y-up
(x east, y up, z south); prints east/north too.  python find_pads.py <scene.ply> <east> <north> [R=12]"""
import json, sys
import numpy as np
from plyfile import PlyData
ply, e, n = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]); R = float(sys.argv[4]) if len(sys.argv) > 4 else 12
v = PlyData.read(ply)["vertex"].data; x, y, z = v["x"], v["y"], v["z"]
near = (x - e) ** 2 + (z + n) ** 2 < R * R
c = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][near] * 0.28209479177387814 + 0.5
op = 1 / (1 + np.exp(-v["opacity"][near])); P = np.c_[x[near], y[near], z[near]]
ground = np.median(P[op > 0.5, 1]); low = (op > 0.5) & (P[:, 1] > ground - 0.2) & (P[:, 1] < ground + 0.6)
r, g, b = c.T
kinds = {"red": (r > 0.45) & (r > 1.8 * g) & (r > 1.8 * b), "blue": (b > 0.35) & (b > 1.5 * r) & (b > 1.3 * g),
         "yellow": (r > 0.5) & (g > 0.45) & (b < 0.6 * g), "black": (r + g + b < 0.25)}
out = {}
for k, m in kinds.items():
    Q = P[m & low]
    if len(Q) < 15: print(f"{k}: {len(Q)} splats, not found"); continue
    med = np.median(Q, axis=0); Q = Q[np.linalg.norm(Q[:, [0, 2]] - med[[0, 2]], axis=1) < 1.5]   # the cluster round the median
    med = np.median(Q, axis=0); out[k] = [round(float(med[0]), 2), round(float(med[1]), 2), round(float(med[2]), 2)]
    print(f"{k}: {len(Q)} splats at east {med[0]:.2f} north {-med[2]:.2f} (y {med[1]:.2f})")
print(json.dumps(out))
