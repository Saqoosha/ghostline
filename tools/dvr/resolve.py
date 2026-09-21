"""DVR path: per frame choose {as registered, moved to another identical arch, rejected} so that the
whole path obeys a speed limit, faces the way it moves, and prefers well-supported registrations;
then Kalman-smooth what is kept."""
import json, sys, re
import numpy as np
def q2R(qw, qx, qy, qz):
    return np.array([[1-2*(qy*qy+qz*qz), 2*(qx*qy-qz*qw), 2*(qx*qz+qy*qw)],[2*(qx*qy+qz*qw), 1-2*(qx*qx+qz*qz), 2*(qy*qz-qx*qw)],[2*(qx*qz-qy*qw), 2*(qy*qz+qx*qw), 1-2*(qx*qx+qy*qy)]])
def load(path, mat):
    g = json.load(open(mat)); s, R, t = g["scale"], np.array(g["R"]), np.array(g["t"]); out = {}
    for line in open(path):
        p = line.split(); Rc = q2R(*map(float, p[1:5])); tv = np.array(list(map(float, p[5:8])))
        out[p[9]] = (R @ Rc.T, s * (R @ (-Rc.T @ tv)) + t)
    return out
old = load(sys.argv[1], sys.argv[2]); new = load(sys.argv[3], sys.argv[4]); dvr = load(sys.argv[5], sys.argv[4])
inl = {l.split()[0]: int(l.split()[1]) for l in open(sys.argv[7])}
bad = [l.strip() for l in open(sys.argv[6]) if l.strip()]
SEG = {"A": (98.3, 101.3), "C": (155.8, 169.4), "D": (237.8, 240.2)}
T = {"B": (np.eye(3), np.zeros(3))}
for gname, (t0, t1) in SEG.items():
    Rs, pairs = [], []
    for n in bad:
        tt = int(re.search(r"a_(\d+)", n).group(1)) / 59.94
        if t0 <= tt <= t1: Rs.append(new[n][0] @ old[n][0].T); pairs.append((old[n][1], new[n][1]))
    U, _, Vt = np.linalg.svd(sum(Rs)); Rt = U @ np.diag([1, 1, np.linalg.det(U @ Vt)]) @ Vt
    T[gname] = (Rt, np.mean([b - Rt @ a for a, b in pairs], axis=0))
centre = {"A": np.array([3.6, 0, -15.2]), "C": np.array([43.6, 0, 43.0]), "D": np.array([3.3, 0, 33.9])}
centre["B"] = T["A"][0].T @ (centre["A"] - T["A"][1])
frames = sorted(dvr, key=lambda n: int(re.search(r"dvr_(\d+)", n).group(1)))
keep, last = [], -99
for n in frames:
    k = int(re.search(r"dvr_(\d+)", n).group(1))
    if k - last >= 1: keep.append(n); last = k          # gap frames are consecutive 60 fps frames
frames = keep; N = len(frames)
ts = np.array([int(re.search(r"dvr_(\d+)", n).group(1)) / 60 + 80 for n in frames])
cands = []                                     # per frame: list of (label, centre, forward, unary)
for n in frames:
    Rw, C = dvr[n]; f = Rw @ np.array([0, 0, 1.0]); q = 40.0 / max(inl.get(n, 25), 25)   # 0.25 (160 pts) .. 1.6 (25 pts)
    c = [("as-is", C, f, q, Rw)]
    near = [g for g in centre if np.hypot(*(C - centre[g])[[0, 2]]) < 14]
    if near:
        x = min(near, key=lambda g: np.hypot(*(C - centre[g])[[0, 2]])); Rx, tx = T[x]
        for y in T:
            if y == x: continue
            Ry, ty = T[y]; M = Ry @ Rx.T
            c.append((f"{x}->{y}", M @ (C - tx) + ty, M @ f, q + 0.6, M @ Rw))
    cands.append(c)
VMAX, VREF, REJECT, MAXGAP = 47.0, 30.0, 2.5, 2.5      # m/s, m/s, cost per dropped frame, s
def trans(Ci, fi, Cj, fj, dt):
    # Only the physics: a soft cost that grows with speed and a hard wall above VMAX. No heading
    # term - an FPV quad in a hairpin faces the exit while momentum carries it sideways, and the
    # heading prior threw away exactly the apex frames (97.9-98.2 s: 5 m cut off the corner).
    v = np.linalg.norm(Cj - Ci) / max(dt, 0.15)      # position noise (~0.5 m) over 1/60 s is not a speed
    # The wall must be steep: at 30x a 53 m/s jump onto the wrong arch cost 0.1 and won over the
    # relabel, and a 58 m/s jump onto a wrong cluster cost 0.7 - cheaper than rejecting it.
    return 0.3 * (v / VMAX) ** 2 * min(dt, 1.0) + (300 * (v / VMAX - 1) ** 2 if v > VMAX else 0)
INF = 1e18; best = [[INF] * len(c) for c in cands]; back = [[None] * len(c) for c in cands]
for k in range(len(cands[0])): best[0][k] = cands[0][k][3]
for i in range(1, N):
    for k, (_, Ci, fi, ui, _R) in enumerate(cands[i]):
        b, bp = REJECT * i + ui, None                                    # everything before rejected
        for j in range(i - 1, -1, -1):
            dt = ts[i] - ts[j]
            if dt > MAXGAP: break
            for m, (_, Cj, fj, _, _R2) in enumerate(cands[j]):
                if best[j][m] >= INF: continue
                cst = best[j][m] + REJECT * (i - j - 1) + trans(Cj, fj, Ci, fi, dt) + ui
                if cst < b: b, bp = cst, (j, m)
        best[i][k], back[i][k] = b, bp
# best end: any frame i, plus rejecting the tail
end = min(((best[i][k] + REJECT * (N - 1 - i), i, k) for i in range(N) for k in range(len(cands[i]))))
kept = {}; i, k = end[1], end[2]
while i is not None:
    kept[i] = k; nxt = back[i][k]; i, k = (nxt if nxt else (None, None))
out = []
for i, n in enumerate(frames):
    if i in kept:
        lab, C, f, _, Rw = cands[i][kept[i]]; out.append({"t": float(ts[i]), "name": n, "label": lab, "pos": C.tolist(), "fwd": f.tolist(), "R": Rw.tolist(), "orig": cands[i][0][1].tolist(), "inliers": inl.get(n, 0)})
    else: out.append({"t": float(ts[i]), "name": n, "label": "rejected", "pos": None, "orig": cands[i][0][1].tolist(), "inliers": inl.get(n, 0)})
# Kalman (constant velocity) + RTS on the kept frames, irregular dt. Then drop measurements the
# smoother disagrees with by more than OUT_M and smooth again: a single wrong registration that the
# speed test let through still tugs the curve by metres, and this is where it shows.
q, r, OUT_M = 40.0, 0.4, 2.0
def rts(K):
    tk = np.array([x["t"] for x in K]); Z = np.array([x["pos"] for x in K])
    xs, Ps, xp, Pp = [], [], [], []; x = np.r_[Z[0], 0, 0, 0]; P = np.diag([r*r]*3 + [400]*3); H = np.c_[np.eye(3), np.zeros((3, 3))]
    for i in range(len(K)):
        if i > 0:
            dt = tk[i] - tk[i-1]; F = np.eye(6); F[:3, 3:] = dt * np.eye(3); G = np.r_[0.5*dt*dt*np.eye(3), dt*np.eye(3)]; x = F @ x; P = F @ P @ F.T + q*q * G @ G.T
        xp.append(x.copy()); Pp.append(P.copy())
        S = H @ P @ H.T + r*r*np.eye(3); Kg = P @ H.T @ np.linalg.inv(S); x = x + Kg @ (Z[i] - H @ x); P = (np.eye(6) - Kg @ H) @ P; xs.append(x.copy()); Ps.append(P.copy())
    xsm = [None] * len(K); xsm[-1] = xs[-1]
    for i in range(len(K) - 2, -1, -1):
        dt = tk[i+1] - tk[i]; F = np.eye(6); F[:3, 3:] = dt * np.eye(3); Cg = Ps[i] @ F.T @ np.linalg.inv(Pp[i+1]); xsm[i] = xs[i] + Cg @ (xsm[i+1] - xp[i+1])
    return np.array(xsm)
n_out = 0
for _ in range(4):
    K = [x for x in out if x["pos"] is not None]; X = rts(K)
    res = np.linalg.norm(np.array([x["pos"] for x in K]) - X[:, :3], axis=1); bad = res > OUT_M
    for x, s_, b in zip(K, X, bad):
        if b: x["label"] = "outlier"; x["pos"] = None; x.pop("smooth", None); x.pop("vel", None); n_out += 1
        else: x["smooth"] = s_[:3].tolist(); x["vel"] = s_[3:].tolist()
    if not bad.any(): break
K = [x for x in out if x["pos"] is not None]
json.dump(out, open(sys.argv[8], "w"))
nrej = sum(1 for x in out if x["pos"] is None) - n_out; nmov = sum(1 for x in out if x["pos"] is not None and x["label"] != "as-is")
v = np.linalg.norm([x["vel"] for x in K], axis=1) * 3.6
print(f"{N} frames: kept {N-nrej-n_out}, rejected {nrej}, smoother outliers {n_out}, moved between arches {nmov}")
print("moved:", " ".join(f"{x['t']:.1f}{x['label'][1:]}" for x in K if x["label"] != "as-is"))
print("rejected at:", " ".join(f"{x['t']:.1f}" for x in out if x["pos"] is None))
print(f"smoothed speed: median {np.median(v):.0f} km/h, p95 {np.percentile(v,95):.0f}, max {v.max():.0f}")
