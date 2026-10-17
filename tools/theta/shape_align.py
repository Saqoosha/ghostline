"""Coarse similarity between today's SfM and the old scan from their shapes alone: top-down maps of what stands above
the ground (tree lines, buildings, the tower), searched over yaw and scale, translation by FFT cross-correlation.
  shape_align.py <old.ply> <sparse dir> <out.json> [scale lo] [scale hi]
Old scan: y up. SfM: up = the points' least-spread axis, signed so the cameras sit above them.
out.json: {"scale", "R", "t"} with x_old = scale * R @ x_sfm + t, plus the score and its runner-up."""
import sys, json, struct, numpy as np
from plyfile import PlyData
from scipy.ndimage import gaussian_filter

old_ply, sparse, out = sys.argv[1:4]
S_LO, S_HI = (float(sys.argv[4]), float(sys.argv[5])) if len(sys.argv) > 5 else (25.0, 60.0)
CELL, TALL = 1.0, 4.0                                     # m per cell; metres above the ground that count as standing

def tallmap(P, ground, lo, n):                            # P: (N,3) in a y-up metric frame -> occupancy of standing things
    k = P[:, 1] > ground + TALL
    i = ((P[k, 0] - lo[0]) / CELL).astype(int); j = ((P[k, 2] - lo[1]) / CELL).astype(int)
    m = (i >= 0) & (i < n) & (j >= 0) & (j < n); g = np.zeros((n, n))
    np.add.at(g, (j[m], i[m]), 1.0)
    return gaussian_filter(np.log1p(g), 1.5)

v = PlyData.read(old_ply)["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.3]
O = np.c_[v["x"], v["y"], v["z"]]; og = np.percentile(O[:, 1], 5)
N = 512; olo = np.array([-256.0, -256.0])
G_old = tallmap(O, og, olo, N); F_old = np.fft.rfft2(G_old - G_old.mean())

X = []
with open(f"{sparse}/points3D.bin", "rb") as f:
    n, = struct.unpack("<Q", f.read(8))
    for k in range(n):
        _i, x, y, z, r, g, b, e, tl = struct.unpack("<QdddBBBdQ", f.read(51)); f.seek(8 * tl, 1)
        if k % 4 == 0: X.append((x, y, z))
X = np.array(X)
C = []
with open(f"{sparse}/images.bin", "rb") as f:
    n, = struct.unpack("<Q", f.read(8))
    for _ in range(n):
        _id, qw, qx, qy, qz, tx, ty, tz, cam = struct.unpack("<idddddddi", f.read(64))
        while f.read(1) != b"\0": pass
        npt, = struct.unpack("<Q", f.read(8)); f.seek(24 * npt, 1)
        w, x, y, z = np.array([qw, qx, qy, qz]) / np.linalg.norm([qw, qx, qy, qz])
        R = np.array([[1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)], [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)], [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)]])
        C.append(-R.T @ np.array([tx, ty, tz]))
C = np.array(C); mu = np.median(C, 0)
X = X[np.linalg.norm(X - mu, axis=1) < 3.0]               # drop the far sky noise
lo, hi = np.percentile(X, 1, 0), np.percentile(X, 99, 0); Xc = X[np.all((X > lo) & (X < hi), 1)]
Vt = np.linalg.svd(Xc - Xc.mean(0), full_matrices=False)[2]; up = Vt[2]
if np.median((C - Xc.mean(0)) @ up) < 0: up = -up
e1 = Vt[0]; e2 = np.cross(e1, up)                          # right-handed (e1, up, e2) = (x, y, z) of a y-up frame
B = np.stack([e1, up, e2]); assert np.linalg.det(B) > 0                                 # rows: SfM -> local y-up
L = (X - mu) @ B.T; lg = np.percentile(L[:, 1], 5)

best = []
for s in np.exp(np.linspace(np.log(S_LO), np.log(S_HI), 36)):
    for yaw in np.radians(np.arange(0, 360, 2)):
        for flip in (1,):
            c, si = np.cos(yaw), np.sin(yaw); Ry = np.array([[c, 0, si], [0, 1, 0], [-si, 0, c]])
            P = s * (L @ Ry.T); P[:, 1] -= s * lg - og      # ground to the old ground
            G = tallmap(P, og, np.array([-N / 2 * CELL, -N / 2 * CELL]), N)
            if G.sum() == 0: continue
            cc = np.fft.irfft2(F_old * np.conj(np.fft.rfft2(G - G.mean())), s=(N, N))
            j, i = np.unravel_index(np.argmax(cc), cc.shape)
            score = cc[j, i] / (np.linalg.norm(G - G.mean()) * np.linalg.norm(G_old - G_old.mean()))
            di, dj = (i if i < N // 2 else i - N), (j if j < N // 2 else j - N)
            best.append((score, s, yaw, di * CELL + olo[0] + N / 2 * CELL, dj * CELL + olo[1] + N / 2 * CELL))
best.sort(key=lambda r: -r[0])
for r in best[:8]: print(f"score {r[0]:.3f} scale {r[1]:.2f} yaw {np.degrees(r[2]):.0f} shift {r[3]:.1f},{r[4]:.1f}")
score, s, yaw, tx, tz = best[0]
c, si = np.cos(yaw), np.sin(yaw); Ry = np.array([[c, 0, si], [0, 1, 0], [-si, 0, c]])
R = Ry @ B                                                  # x_old = s * R @ (x - mu) + [tx, og - s*lg, tz]
t = np.array([tx, og - s * lg, tz]) - s * R @ mu
# the runner-up that is not a neighbour of the best: how sure the peak is
far = [r for r in best if abs(r[1] / s - 1) > 0.08 or abs((np.degrees(r[2] - yaw) + 180) % 360 - 180) > 10]
json.dump(dict(scale=float(s), R=R.tolist(), t=t.tolist(), score=float(score), runner_up=float(far[0][0]) if far else None), open(out, "w"), indent=1)
print("cameras at", np.round(s * (R @ C.T).T.mean(0) + t, 1), "| runner-up elsewhere", round(far[0][0], 3) if far else None)

# ---- overlays of the best and the runner-up elsewhere: old scan top (grey = standing, colours = ground), today's
# standing points red, cameras blue. One panel each, 200 m across, centred on the cameras.
import cv2
def panel(r):
    sc, yw, tx_, tz_ = r[1], r[2], r[3], r[4]
    c_, s_ = np.cos(yw), np.sin(yw); Ry_ = np.array([[c_, 0, s_], [0, 1, 0], [-s_, 0, c_]])
    P = sc * (L @ Ry_.T); P[:, 1] -= sc * lg - og; P[:, 0] += tx_; P[:, 2] += tz_
    Pc = sc * (((C - mu) @ B.T) @ Ry_.T); Pc[:, 0] += tx_; Pc[:, 2] += tz_
    cx, cz = Pc[:, 0].mean(), Pc[:, 2].mean(); px, half = 700, 100.0
    to = lambda A: (((A[:, 0] - cx + half) / (2 * half) * px).astype(int), ((A[:, 2] - cz + half) / (2 * half) * px).astype(int))
    img = np.full((px, px, 3), 255, np.uint8)
    cols = np.clip(0.5 + 0.282 * np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], 0, 1)
    tall = O[:, 1] > og + TALL
    for m, col in ((~tall, None), (tall, (120, 120, 120))):
        u, w = to(O[m]); k = (u >= 0) & (u < px) & (w >= 0) & (w < px)
        img[w[k], u[k]] = (cols[m][k][:, ::-1] * 255).astype(np.uint8) if col is None else col
    u, w = to(P[P[:, 1] > og + TALL]); k = (u >= 0) & (u < px) & (w >= 0) & (w < px); img[w[k], u[k]] = (0, 0, 230)
    u, w = to(Pc); k = (u >= 0) & (u < px) & (w >= 0) & (w < px); img[w[k], u[k]] = (230, 80, 0)
    cv2.putText(img, f"score {r[0]:.3f} scale {sc:.1f} yaw {np.degrees(yw):.0f}", (10, 25), 0, 0.7, (0, 0, 0), 2)
    return img
cv2.imwrite(out.replace(".json", ".jpg"), np.hstack([panel(best[0])] + ([panel(far[0])] if far else [])))
