"""Bundle-adjust the DVR camera against the fixed scan: one shared OPENCV_FISHEYE (fx fy cx cy k1..k4)
plus a 6-DoF pose per DVR image, from the 2D-3D correspondences COLMAP's image_registrator left in
images.txt. The scan's 3D points stay fixed, so the metric frame cannot move. Robust (Cauchy) loss."""
import sys, re, json, numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation as Rot
model, out = sys.argv[1], sys.argv[2]
cam = [l.split() for l in open(f"{model}/cameras.txt") if "FISHEYE" in l][0]; cam_id = cam[0]; p0 = np.array(cam[4:], float)
X = {}
for l in open(f"{model}/points3D.txt"):
    if l[0] != "#": f = l.split(); X[int(f[0])] = [float(f[1]), float(f[2]), float(f[3])]
L = [l for l in open(f"{model}/images.txt") if l[0] != "#"]; imgs = []
for a, b in zip(L[0::2], L[1::2]):
    f = a.split()
    if f[8] != cam_id: continue
    q = np.array(f[1:5], float); t = np.array(f[5:8], float); pts = b.split(); ob = []
    for k in range(0, len(pts), 3):
        pid = int(pts[k+2])
        if pid != -1 and pid in X: ob.append((float(pts[k]), float(pts[k+1]), pid))
    if len(ob) >= 12: imgs.append((f[9], Rot.from_quat([q[1], q[2], q[3], q[0]]).as_rotvec(), t, ob))
n = len(imgs); nobs = sum(len(i[3]) for i in imgs); print(f"{n} DVR images, {nobs} observations, start params {p0.round(4)}")
uv = np.array([(o[0], o[1]) for i in imgs for o in i[3]]); P3 = np.array([X[o[2]] for i in imgs for o in i[3]])
idx = np.concatenate([[k] * len(i[3]) for k, i in enumerate(imgs)])
x0 = np.concatenate([p0] + [np.r_[i[1], i[2]] for i in imgs])
def project(p, rv, tv, P):
    Pc = Rot.from_rotvec(rv).apply(P) + tv; z = np.maximum(Pc[:, 2], 1e-6); a, b = Pc[:, 0] / z, Pc[:, 1] / z
    r = np.sqrt(a*a + b*b) + 1e-12; th = np.arctan(r); th2 = th*th
    thd = th * (1 + p[4]*th2 + p[5]*th2**2 + p[6]*th2**3 + p[7]*th2**4); s = thd / r
    return np.c_[p[0]*a*s + p[2], p[1]*b*s + p[3]]
def resid(x):
    p = x[:8]; poses = x[8:].reshape(n, 6); return (project(p, poses[idx, :3], poses[idx, 3:], P3) - uv).ravel()
A = lil_matrix((2 * nobs, 8 + 6 * n), dtype=int); rows = np.arange(nobs)
for c in range(8): A[2*rows, c] = 1; A[2*rows+1, c] = 1
for c in range(6): A[2*rows, 8 + 6*idx + c] = 1; A[2*rows+1, 8 + 6*idx + c] = 1
r0 = resid(x0).reshape(-1, 2); e0 = np.linalg.norm(r0, axis=1); print(f"before: reprojection p50 {np.median(e0):.2f} px  p90 {np.percentile(e0,90):.2f}  mean {e0.mean():.2f}")
sol = least_squares(resid, x0, jac_sparsity=A, loss="cauchy", f_scale=2.0, x_scale="jac", method="trf", max_nfev=60, verbose=0)
e1 = np.linalg.norm(resid(sol.x).reshape(-1, 2), axis=1); p1 = sol.x[:8]
print(f"after : reprojection p50 {np.median(e1):.2f} px  p90 {np.percentile(e1,90):.2f}  mean {e1.mean():.2f}   ({sol.nfev} evals)")
print("params fx fy cx cy k1 k2 k3 k4"); print("  was", p0.round(4)); print("  now", p1.round(4))
poses = sol.x[8:].reshape(n, 6); moved = []
with open(out + ".poses.txt", "w") as f:
    for (name, rv0, t0, _), pz in zip(imgs, poses):
        q = Rot.from_rotvec(pz[:3]).as_quat(); f.write(f"0 {q[3]} {q[0]} {q[1]} {q[2]} {pz[3]} {pz[4]} {pz[5]} {cam_id} {name}\n")
        C0 = -Rot.from_rotvec(rv0).inv().apply(t0); C1 = -Rot.from_rotvec(pz[:3]).inv().apply(pz[3:]); moved.append(np.linalg.norm(C1 - C0))
moved = np.array(moved) * 10.954
print(f"camera centres moved: p50 {np.median(moved):.2f} m  p90 {np.percentile(moved,90):.2f}  max {moved.max():.2f}")
json.dump({"model": "OPENCV_FISHEYE", "width": 960, "height": 720, "params": p1.tolist(), "was": p0.tolist(), "reproj_px_p50": float(np.median(e1)), "images": n, "observations": nobs}, open(out + ".json", "w"), indent=1)
