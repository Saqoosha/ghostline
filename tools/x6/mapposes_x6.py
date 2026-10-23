"""Poses for rt_map.py from an RTK-constrained X6 SfM (spirula-rtk: metric, the frame of the positions it was given - ENU, z up),
in the scene's y-up frame (x east, y up, z south: the same rotation is applied to the splats with fit_transform.py).
Every STEP-th rig position (cam0), as level views every 60 deg at the walker's height and DY metres above it: a lens of the 360
faces any way, so the views are made, not taken from it. -> {"cameras": [{name, pos, quat}]} and the rotation as fit_transform's json.
  python mapposes_x6.py <sparse dir> <map_poses.json> <enu2yup.json> [STEP=8] [DY=0,2,5]"""
import json, math, struct, sys
import numpy as np
from scipy.spatial.transform import Rotation as Rot
sparse, out, tf = sys.argv[1:4]; step = int(sys.argv[4]) if len(sys.argv) > 4 else 8
dys = [float(v) for v in (sys.argv[5] if len(sys.argv) > 5 else "0,2,5").split(",")]
A = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)   # ENU -> (east, up, south): right-handed, det +1
json.dump(dict(scale=1.0, R=A.tolist(), t=[0, 0, 0]), open(tf, "w"))
pos = []
with open(f"{sparse}/images.bin", "rb") as f:
    n, = struct.unpack("<Q", f.read(8))
    for _ in range(n):
        _id, qw, qx, qy, qz, tx, ty, tz, _c = struct.unpack("<idddddddi", f.read(64)); name = b""
        while (ch := f.read(1)) != b"\0": name += ch
        npt, = struct.unpack("<Q", f.read(8)); f.seek(24 * npt, 1); name = name.decode()
        if "/cam0/" not in name: continue
        R = Rot.from_quat([qx, qy, qz, qw]).as_matrix()
        pos.append((name, A @ (-R.T @ np.array([tx, ty, tz]))))
pos.sort()
cams = []
for name, c in pos[::step]:
    for dy in dys:
        for k in range(6):
            a = math.radians(60 * k); fwd = np.array([math.cos(a), 0, math.sin(a)]); down = np.array([0, -1.0, 0])
            M = np.c_[np.cross(down, fwd), down, fwd]   # camera x right, y down, z forward, as mapposes.py
            p = c + np.array([0, dy, 0])
            cams.append(dict(name=f"{name}+{dy:g}m@{60 * k}", pos=p.round(4).tolist(), quat=Rot.from_matrix(M).as_quat().round(6).tolist()))
json.dump(dict(frame="ENU from the RTK SfM, as (east, up, south)", cameras=cams), open(out, "w"))
ys = np.array([c for _, c in pos])[:, 1]
print(f"{len(pos)} rig positions, every {step}th x {len(dys)} heights x 6 -> {len(cams)} poses; walker height (up) p50 {np.median(ys):.2f} m -> {out}")
