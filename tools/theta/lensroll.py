"""Per lens folder: COLMAP camera id and the roll (deg) that puts the world's up at the top of a pinhole view.
Up = the points' least-spread axis, signed so the cameras sit above them (as sfm_view.py)."""
import sys, struct, numpy as np, collections
sp = sys.argv[1]
def imgs(p):
    out = []
    with open(p, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            _id, qw, qx, qy, qz, tx, ty, tz, cam = struct.unpack("<idddddddi", f.read(64)); name = b""
            while (ch := f.read(1)) != b"\0": name += ch
            npt, = struct.unpack("<Q", f.read(8)); f.seek(24 * npt, 1)
            w, x, y, z = np.array([qw, qx, qy, qz]) / np.linalg.norm([qw, qx, qy, qz])
            R = np.array([[1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)], [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)], [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)]])
            out.append((name.decode(), cam, R, -R.T @ np.array([tx, ty, tz])))
    return out
def pts(p, step=20):
    X = []
    with open(p, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        for k in range(n):
            _i, x, y, z, r, g, b, e, tl = struct.unpack("<QdddBBBdQ", f.read(51)); f.seek(8 * tl, 1)
            if k % step == 0: X.append((x, y, z))
    return np.array(X)
I = imgs(f"{sp}/images.bin"); X = pts(f"{sp}/points3D.bin")
lo, hi = np.percentile(X, 1, 0), np.percentile(X, 99, 0); X = X[np.all((X > lo) & (X < hi), 1)]
up = np.linalg.svd(X - X.mean(0), full_matrices=False)[2][2]
C = np.array([c for *_, c in I])
if np.median((C - X.mean(0)) @ up) < 0: up = -up
g = collections.defaultdict(list); cid = {}
for name, cam, R, c in I:
    lens = name.rsplit("/", 1)[0]; cid[lens] = cam; u = R @ up
    g[lens].append(np.degrees(np.arctan2(u[0], -u[1])))
for lens in sorted(g):
    a = np.radians(g[lens]); m = np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean()))
    print(lens, cid[lens], round(float(m), 1), "spread", round(float(np.degrees(np.std(np.angle(np.exp(1j * (a - np.radians(m))))))), 1))
