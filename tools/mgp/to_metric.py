"""Move a 3DGS PLY (or anything with SfM coordinates) from the SfM frame into the VelociDrone
track frame in metres: x = VelociDrone x, y = VelociDrone z, z = up (right-handed).
Chain: SfM -> ground frame (topview.py's *_frame.json) -> 2D similarity onto the track line
(fit2d.py's .json). Splat rotations and scales follow; higher SH bands are dropped (they would
need rotating too).
usage: to_metric.py <in.ply> <frame.json> <fit.json> <out.ply>"""
import json, sys
import numpy as np

src, frame, fit, dst = sys.argv[1:5]
F, T = json.load(open(frame)), json.load(open(fit))
E = np.array([F["ex"], F["ey"], F["up"]])                    # rows: ground axes in SfM coords
R2 = np.array(T["R"]) @ np.diag([1, T["mirror"]])
M = np.eye(3); M[:2, :2] = R2
A = T["scale"] * M @ E                                       # SfM -> metric, linear part
b = np.r_[T["t"], 0] - A @ np.array(F["origin"])
s = T["scale"]
Rm = A / s
assert np.linalg.det(Rm) > 0, "mirror fit: splats would need a reflection"

raw = open(src, "rb").read()
end = raw.index(b"end_header\n") + len(b"end_header\n")
head = raw[:end].decode()
props = [l.split()[-1] for l in head.splitlines() if l.startswith("property")]
n = int([l for l in head.splitlines() if l.startswith("element vertex")][0].split()[-1])
v = np.frombuffer(raw[end:], np.float32, n * len(props)).reshape(n, len(props)).copy()
ix = {p: i for i, p in enumerate(props)}

xyz = v[:, [ix["x"], ix["y"], ix["z"]]]
v[:, [ix["x"], ix["y"], ix["z"]]] = xyz @ A.T + b
for k in ("scale_0", "scale_1", "scale_2"):
    v[:, ix[k]] += np.log(s)
q = v[:, [ix["rot_0"], ix["rot_1"], ix["rot_2"], ix["rot_3"]]]   # wxyz
# rotation matrix -> quaternion (wxyz), then left-multiply
tr = np.trace(Rm)
qw = np.sqrt(max(0.0, 1 + tr)) / 2
qx = np.copysign(np.sqrt(max(0.0, 1 + Rm[0, 0] - Rm[1, 1] - Rm[2, 2])) / 2, Rm[2, 1] - Rm[1, 2])
qy = np.copysign(np.sqrt(max(0.0, 1 - Rm[0, 0] + Rm[1, 1] - Rm[2, 2])) / 2, Rm[0, 2] - Rm[2, 0])
qz = np.copysign(np.sqrt(max(0.0, 1 - Rm[0, 0] - Rm[1, 1] + Rm[2, 2])) / 2, Rm[1, 0] - Rm[0, 1])
a0, a1, a2, a3 = qw, qx, qy, qz
b0, b1, b2, b3 = q.T
v[:, ix["rot_0"]] = a0 * b0 - a1 * b1 - a2 * b2 - a3 * b3
v[:, ix["rot_1"]] = a0 * b1 + a1 * b0 + a2 * b3 - a3 * b2
v[:, ix["rot_2"]] = a0 * b2 - a1 * b3 + a2 * b0 + a3 * b1
v[:, ix["rot_3"]] = a0 * b3 + a1 * b2 - a2 * b1 + a3 * b0
for p in props:
    if p.startswith("f_rest_"):
        v[:, ix[p]] = 0
open(dst, "wb").write(head.encode() + v.astype(np.float32).tobytes())
P = v[:, [ix["x"], ix["y"], ix["z"]]]
print("splats", n, "scale m/unit", round(s, 3), "p5/p50/p95 xyz", np.percentile(P, [5, 50, 95], 0).round(1).tolist())
