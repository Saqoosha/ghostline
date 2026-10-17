# /// script
# dependencies = ["numpy", "matplotlib"]
# ///
"""Top and side views of a COLMAP sparse model: points in their colours, cameras coloured by video folder.
  uv run sfm_view.py <sparse dir> <out.png>
Up is the points' least-spread principal axis (the field is flat), signed so the cameras sit above the ground."""
import sys, struct, numpy as np, matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt

sparse, out = sys.argv[1], sys.argv[2]

def points(path):
    X, C = [], []
    with open(path, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            b = f.read(51); _id, x, y, z, r, g, bl, err, tl = struct.unpack("<QdddBBBdQ", b)
            f.seek(8 * tl, 1); X.append((x, y, z)); C.append((r, g, bl))
    return np.array(X), np.array(C) / 255

def images(path):
    out = []
    with open(path, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            _id, qw, qx, qy, qz, tx, ty, tz, _cam = struct.unpack("<idddddddi", f.read(64))
            name = b""
            while (ch := f.read(1)) != b"\0": name += ch
            npt, = struct.unpack("<Q", f.read(8)); f.seek(24 * npt, 1)
            w, x, y, z = np.array([qw, qx, qy, qz]) / np.linalg.norm([qw, qx, qy, qz])
            R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                          [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                          [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
            out.append((name.decode(), -R.T @ np.array([tx, ty, tz])))
    return out

X, C = points(f"{sparse}/points3D.bin"); cams = images(f"{sparse}/images.bin")
P = np.array([c for _, c in cams]); vid = np.array([n.split("/")[0] for n, _ in cams])
lo, hi = np.percentile(X, 1, 0), np.percentile(X, 99, 0); keep = np.all((X > lo) & (X < hi), 1)
mu = X[keep].mean(0); _, _, Vt = np.linalg.svd(X[keep] - mu, full_matrices=False)
ax_u, ax_v, up = Vt[0], Vt[1], Vt[2]
if np.median((P - mu) @ up) < 0: up, ax_v = -up, -ax_v
proj = lambda A: np.c_[(A - mu) @ ax_u, (A - mu) @ ax_v, (A - mu) @ up]
x, pc = proj(X[keep]), proj(P)
fig, (a, b) = plt.subplots(2, 1, figsize=(16, 13), gridspec_kw=dict(height_ratios=[3, 1]))
for ax, (i, j), title in ((a, (0, 1), "top"), (b, (0, 2), "side")):
    ax.scatter(x[:, i], x[:, j], s=0.3, c=C[keep], linewidths=0)
    for v, col in zip(sorted(set(vid)), ("tab:red", "tab:blue", "tab:orange", "tab:purple")):
        m = vid == v; ax.plot(pc[m, i], pc[m, j], ".", ms=2, color=col, label=f"{v} ({m.sum()} images)")
    ax.set_aspect("equal"); ax.set_title(title); ax.legend(loc="upper right", markerscale=6)
h = {v: np.median(pc[vid == v, 2]) for v in sorted(set(vid))}
g = np.percentile(x[:, 2], 5)
fig.suptitle(f"{sparse}: {len(X)} points, {len(P)} cameras; camera height above the 5th-pct point level (SfM units): "
             + ", ".join(f"{v} {h[v] - g:.2f}" for v in h))
plt.tight_layout(); plt.savefig(out, dpi=110); print(out, {v: round(float(h[v] - g), 3) for v in h})
