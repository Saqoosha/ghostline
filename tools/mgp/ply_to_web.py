"""to_metric.py's track frame (x = VelociDrone x, y = VelociDrone z, z = up) -> the viewer's web frame
(x = VelociDrone x, y = up, z = -VelociDrone z): a 90 degree turn about x applied to splat centres and rotations.
usage: ply_to_web.py <in.ply> <out.ply>"""
import sys
import numpy as np

R = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)        # web = R @ track
q_r = np.array([np.sqrt(0.5), -np.sqrt(0.5), 0, 0])             # wxyz of R (rotation of -90 deg about x)
raw = open(sys.argv[1], "rb").read()
end = raw.index(b"end_header\n") + len(b"end_header\n")
head = raw[:end].decode()
props = [l.split()[-1] for l in head.splitlines() if l.startswith("property")]
n = int([l for l in head.splitlines() if l.startswith("element vertex")][0].split()[-1])
v = np.frombuffer(raw[end:], np.float32, n * len(props)).reshape(n, len(props)).copy()
ix = {p: i for i, p in enumerate(props)}
v[:, [ix["x"], ix["y"], ix["z"]]] = v[:, [ix["x"], ix["y"], ix["z"]]] @ R.T
a0, a1, a2, a3 = q_r
b0, b1, b2, b3 = (v[:, ix[f"rot_{k}"]].copy() for k in range(4))
v[:, ix["rot_0"]] = a0 * b0 - a1 * b1 - a2 * b2 - a3 * b3
v[:, ix["rot_1"]] = a0 * b1 + a1 * b0 + a2 * b3 - a3 * b2
v[:, ix["rot_2"]] = a0 * b2 - a1 * b3 + a2 * b0 + a3 * b1
v[:, ix["rot_3"]] = a0 * b3 + a1 * b2 - a2 * b1 + a3 * b0
open(sys.argv[2], "wb").write(head.encode() + v.astype(np.float32).tobytes())
P = v[:, [ix["x"], ix["y"], ix["z"]]]
print("splats", n, "p5/p50/p95 xyz", np.percentile(P, [5, 50, 95], 0).round(1).tolist())
