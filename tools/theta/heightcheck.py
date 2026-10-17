"""Camera heights above the old scan's ground after the similarity: ground under each camera = 10th pct of y of the
old splats within 1.5 m (xz). Old scan is y up."""
import sys, json, struct, numpy as np
from plyfile import PlyData; from scipy.spatial import cKDTree
v = PlyData.read(sys.argv[1])["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.3]
O = np.c_[v["x"], v["y"], v["z"]]; tree = cKDTree(O[:, [0, 2]])
T = json.load(open(sys.argv[3])); s, R, t = T["scale"], np.array(T["R"]), np.array(T["t"])
cams = {}
with open(f"{sys.argv[2]}/images.bin", "rb") as f:
    n, = struct.unpack("<Q", f.read(8))
    for _ in range(n):
        _id, qw, qx, qy, qz, tx, ty, tz, _c = struct.unpack("<idddddddi", f.read(64)); name = b""
        while (ch := f.read(1)) != b"\0": name += ch
        npt, = struct.unpack("<Q", f.read(8)); f.seek(24 * npt, 1)
        w, x, y, z = np.array([qw, qx, qy, qz]) / np.linalg.norm([qw, qx, qy, qz])
        Rm = np.array([[1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)], [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)], [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)]])
        cams.setdefault(name.decode().split("/")[0], []).append(s * R @ (-Rm.T @ np.array([tx, ty, tz])) + t)
for vid, P in sorted(cams.items()):
    P = np.array(P[::10]); h = []
    for p in P:
        idx = tree.query_ball_point(p[[0, 2]], 1.5)
        if len(idx) > 20: h.append(p[1] - np.percentile(O[idx, 1], 10))
    h = np.array(h); print(f"{vid}: height above old ground p10 {np.percentile(h,10):.2f} p50 {np.median(h):.2f} p90 {np.percentile(h,90):.2f} m ({len(h)} cams)")
