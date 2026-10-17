"""Poses for rt_map.py on a THETA scene (the FDF 2026-10-17 videos: rolls from lensroll.py, v134 shot level, v132 not), in the old scan's frame (y up): v134's pinhole views as shot, and six level
views (every 60 deg) at each v132 position, whose lenses faced up and down. -> {"cameras": [{name, pos, quat}]}"""
import sys, json, struct, math, numpy as np
from scipy.spatial.transform import Rotation as Rot
sparse, sim, out = sys.argv[1:4]; rolls = {"v134/cam0": -88.7, "v134/cam1": 88.6}
T = json.load(open(sim)); s, RS, t = T["scale"], np.array(T["R"]), np.array(T["t"])
cams = []
with open(f"{sparse}/images.bin", "rb") as f:
    n, = struct.unpack("<Q", f.read(8))
    for _ in range(n):
        _id, qw, qx, qy, qz, tx, ty, tz, _c = struct.unpack("<idddddddi", f.read(64)); name = b""
        while (ch := f.read(1)) != b"\0": name += ch
        npt, = struct.unpack("<Q", f.read(8)); f.seek(24 * npt, 1); name = name.decode(); lens = name.rsplit("/", 1)[0]
        w, x, y, z = np.array([qw, qx, qy, qz]) / np.linalg.norm([qw, qx, qy, qz])
        R = np.array([[1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)], [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)], [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)]])
        c = s * RS @ (-R.T @ np.array([tx, ty, tz])) + t
        if lens in rolls:
            r = math.radians(rolls[lens]); Rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
            cams.append(dict(name=name, pos=c.round(4).tolist(), quat=Rot.from_matrix(RS @ R.T @ Rz).as_quat().round(6).tolist()))
        elif lens == "v132/cam0":
            for k in range(6):
                a = math.radians(60 * k); fwd = np.array([math.cos(a), 0, math.sin(a)]); down = np.array([0, -1.0, 0])
                M = np.c_[np.cross(down, fwd), down, fwd]
                cams.append(dict(name=f"{name}@{60 * k}", pos=c.round(4).tolist(), quat=Rot.from_matrix(M).as_quat().round(6).tolist()))
json.dump(dict(frame="FDF-2026-R6b-spirula-web-dvr2 (y up)", cameras=cams), open(out, "w"))
print(len(cams), "poses ->", out)
