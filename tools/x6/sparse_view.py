"""A COLMAP sparse model at a glance: the points (their colours) from above and from an oblique view, the rig's path, and the
points as a PLY to turn around in a viewer. The model is in the frame of the positions spirula-rtk was given (ENU, metres).
  python sparse_view.py <sparse dir> <out.png> [out.ply] [--course course.json]   (course: {"enu": [[e, n, u], ...]}, drawn in red)"""
import struct, sys
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

def points(path):
    with open(path, "rb") as f:
        n, = struct.unpack("<Q", f.read(8)); xyz = np.empty((n, 3)); rgb = np.empty((n, 3), np.uint8); err = np.empty(n)
        for i in range(n):
            _id, x, y, z, r, g, b, e = struct.unpack("<QdddBBBd", f.read(43)); xyz[i] = x, y, z; rgb[i] = r, g, b; err[i] = e
            tl, = struct.unpack("<Q", f.read(8)); f.seek(8 * tl, 1)
    return xyz, rgb, err

def centers(path):
    out = []
    with open(path, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            _id, qw, qx, qy, qz, tx, ty, tz, _c = struct.unpack("<idddddddi", f.read(64)); name = b""
            while (ch := f.read(1)) != b"\0": name += ch
            npt, = struct.unpack("<Q", f.read(8)); f.seek(24 * npt, 1)
            w, x, y, z = qw, qx, qy, qz
            R = np.array([[1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)], [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)], [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)]])
            out.append((name.decode(), -R.T @ np.array([tx, ty, tz])))
    out.sort(); return np.array([c for n, c in out if "/cam0/" in n])

args = [a for a in sys.argv[1:] if not a.startswith("--")]; course = None
if "--course" in sys.argv: import json; course = np.array(json.load(open(sys.argv[sys.argv.index("--course") + 1]))["enu"])
sp, png = args[0], args[1]
xyz, rgb, err = points(f"{sp}/points3D.bin"); cam = centers(f"{sp}/images.bin")
keep = err <= np.percentile(err, 95)                                 # spirula may write one error for every point
lo, hi = np.percentile(xyz[keep], 1, axis=0), np.percentile(xyz[keep], 99, axis=0)
keep &= np.all((xyz > lo - 20) & (xyz < hi + 20), axis=1)          # far strays out of the picture
P, C = xyz[keep], rgb[keep] / 255.0
fig, (a, b) = plt.subplots(1, 2, figsize=(20, 10))
o = np.argsort(P[:, 2])                                              # higher points drawn last, from above
a.scatter(P[o, 0], P[o, 1], c=C[o], s=0.4, linewidths=0); a.plot(cam[:, 0], cam[:, 1], "-", c="#2f6bff", lw=0.8, label="X6 walk")
if course is not None: a.plot(course[:, 0], course[:, 1], "-", c="#ff2e63", lw=1.2, label="reference flight (DJI GPS)")
a.set_aspect("equal"); a.set_title(f"from above: {keep.sum():,} of {len(xyz):,} points, {len(cam)} rig positions"); a.set_xlabel("east (m)"); a.set_ylabel("north (m)"); a.legend(loc="upper right"); a.grid(alpha=.2)
# oblique: looking north-east from the south-west, 30 degrees down
az, el = np.radians(45), np.radians(30)
right = np.array([np.cos(az), -np.sin(az), 0]); fwd = np.array([np.sin(az), np.cos(az), 0])
up = np.array([0, 0, 1]) * np.cos(el) - fwd * np.sin(el)
depth = P @ (fwd * np.cos(el) + np.array([0, 0, -1]) * np.sin(el)); o = np.argsort(-depth)
b.scatter((P @ right)[o], (P @ up)[o], c=C[o], s=0.4, linewidths=0); b.plot(cam @ right, cam @ up, "-", c="#2f6bff", lw=0.8)
b.set_aspect("equal"); b.set_title("oblique, looking north-east, 30 deg down"); b.axis("off")
fig.savefig(png, dpi=110, bbox_inches="tight"); print(f"{keep.sum()} points, {len(cam)} positions -> {png}")
if len(args) > 2:
    with open(args[2], "wb") as f:
        f.write(f"ply\nformat binary_little_endian 1.0\nelement vertex {len(P)}\nproperty float x\nproperty float y\nproperty float z\n"
                "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n".encode())
        v = np.empty(len(P), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
        v["x"], v["y"], v["z"] = P.T; v["r"], v["g"], v["b"] = rgb[keep].T; f.write(v.tobytes())
    print("->", args[2])
