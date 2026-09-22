"""Pass B of the translation search: the per-frame grid search (pass A) is right on average and
noisy frame to frame (5 m jumps between neighbours on grass). Take pass A's accepted poses, median-
filter the positions over 5 frames, then constant-velocity Kalman + RTS (accel 40 m/s^2, sigma 1.5 m),
and write them into a copy of the init so a second refine (TSEARCH=2) starts from a continuous path.
usage: smooth_pass.py init.json passA.jsonl out_init.json"""
import json, sys, numpy as np
P = json.load(open(sys.argv[1])); poses = P["poses"]
rec = {}
for l in open(sys.argv[2]):
    r = json.loads(l)
    if r["accepted"]: rec[r["i"]] = r
idx = sorted(rec); pos = np.array([rec[i]["pos"] for i in idx]); t = np.array([rec[i]["t"] for i in idx])
# median over 5 neighbours (by index order, within 0.2 s)
med = pos.copy()
for k in range(len(idx)):
    sel = [j for j in range(max(0, k - 2), min(len(idx), k + 3)) if abs(t[j] - t[k]) <= 0.2]
    med[k] = np.median(pos[sel], axis=0)
# Kalman + RTS on the (irregular) measurement times
q, r = 40.0, 1.5
x = np.r_[med[0], 0, 0, 0]; Pc = np.diag([r*r]*3 + [100]*3); xs, Ps, xp, Pp, Fs = [], [], [], [], []
H = np.c_[np.eye(3), np.zeros((3, 3))]
for k in range(len(idx)):
    dt = t[k] - t[k-1] if k else 0.0
    F = np.eye(6); F[:3, 3:] = dt * np.eye(3); G = np.r_[0.5*dt*dt*np.eye(3), dt*np.eye(3)]; Q = q*q * G @ G.T
    if k: x = F @ x; Pc = F @ Pc @ F.T + Q
    xp.append(x.copy()); Pp.append(Pc.copy()); Fs.append(F)
    S_ = H @ Pc @ H.T + r*r*np.eye(3); Kg = Pc @ H.T @ np.linalg.inv(S_); x = x + Kg @ (med[k] - H @ x); Pc = (np.eye(6) - Kg @ H) @ Pc
    xs.append(x.copy()); Ps.append(Pc.copy())
xsm = [None] * len(xs); xsm[-1] = xs[-1]
for k in range(len(xs) - 2, -1, -1):
    C = Ps[k] @ Fs[k+1].T @ np.linalg.inv(Pp[k+1]); xsm[k] = xs[k] + C @ (xsm[k+1] - xp[k+1])
moved = []
for k, i in enumerate(idx):
    p = poses[i]; new = xsm[k][:3]; moved.append(np.linalg.norm(new - np.array(p["pos"])))
    poses[i] = {**p, "pos": np.round(new, 3).tolist(), "quat": rec[i]["quat"], "src": p["src"], "passA": True}
json.dump(P, open(sys.argv[3], "w"))
print(f"{len(idx)} frames smoothed: moved from init p50 {np.median(moved):.2f} m p90 {np.percentile(moved, 90):.2f} max {max(moved):.2f}; median-vs-raw p50 {np.median(np.linalg.norm(med - pos, axis=1)):.2f} m; smooth-vs-median p50 {np.median([np.linalg.norm(xsm[k][:3] - med[k]) for k in range(len(idx))]):.2f} m")
