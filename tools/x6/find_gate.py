"""A gate's place and width from the scan: the red splats (the arches are red) near a guess, 0.4-3 m over the ground, and the
line they lie along. Prints the race.json-style gate {centre, half_width, width_dir} in the scene's y-up frame (x east, y up, z south).
  python find_gate.py <scene.ply> <east> <north> [R=6]"""
import json, sys
import numpy as np
from plyfile import PlyData
ply, e, n = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]); R = float(sys.argv[4]) if len(sys.argv) > 4 else 6
v = PlyData.read(ply)["vertex"].data; x, y, z = v["x"], v["y"], v["z"]
near = (x - e) ** 2 + (z + n) ** 2 < R * R                      # z is south
C0 = 0.28209479177387814; rgb = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][near] * C0 + 0.5
op = 1 / (1 + np.exp(-v["opacity"][near])); P = np.c_[x[near], y[near], z[near]]
ground = np.percentile(P[op > 0.5, 1], 30)
red = (rgb[:, 0] > 0.45) & (rgb[:, 0] > 1.6 * rgb[:, 1]) & (rgb[:, 0] > 1.6 * rgb[:, 2]) & (op > 0.4) & (P[:, 1] > ground + 0.4) & (P[:, 1] < ground + 3)
Q = P[red]
if len(Q) < 30: sys.exit(f"only {len(Q)} red splats near ({e}, {n}): widen R or move the guess")
c = np.median(Q, axis=0); d = Q[:, [0, 2]] - c[[0, 2]]
w = np.linalg.svd(d - d.mean(0), full_matrices=False)[2][0]     # the arch's length across the course
s = d @ w; half = (np.percentile(s, 97) - np.percentile(s, 3)) / 2
gate = dict(centre=[round(float(c[0]), 2), round(float(ground), 2), round(float(c[2]), 2)], half_width=round(float(half), 2),
            width_dir=[round(float(w[0]), 4), 0.0, round(float(w[1]), 4)])
print(f"{len(Q)} red splats; ground {ground:.2f} m; top {np.percentile(Q[:, 1], 97) - ground:.2f} m over it")
print(json.dumps(gate))
