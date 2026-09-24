"""Fuse per-frame CPR poses into one 60 fps track (web frame).
Position: constant-velocity Kalman + RTS on the frame grid. A CPR position is only as good as its near
points (fast turns blur the near field away and the far points pin rotation, not position), so the
measurement sigma comes from the fraction of inliers within 15 m. Two passes: the second drops
measurements more than 3 sigma + 0.5 m from the first pass's smoothed track.
Rotation: the CPR rotation where it agrees with its neighbours, slerp elsewhere, then a Gaussian of ROT_SIGMA
frames (weighted chordal mean). sigma 1 cut the frame-to-frame wobble 0.26 -> 0.09 deg and the mark residual
11.8 -> 11.2 px; sigma 3 lags the turns (14.4 px).
Frames before take-off and frames outside the CPR span keep the start pose.
usage: cpr_fuse.py start_poses.json cpr.jsonl out_poses.json   env: ROT_SIGMA (frames, default 1, 0 = off)"""
import json, sys, os, numpy as np
from scipy.spatial.transform import Rotation as Rot, Slerp
MIN_INL, TAKEOFF, Q_ACC, ROT_OUT = 100, 116, 40.0, 10.0   # TAKEOFF is hdz_0067's; Q_ACC as interp60.py
ROT_SIGMA = float(os.environ.get("ROT_SIGMA", 1))
S = json.load(open(sys.argv[1])); N = len(S["poses"])
C = {r["i"]: r for r in map(json.loads, open(sys.argv[2])) if r.get("inliers", 0) >= MIN_INL and r["i"] >= TAKEOFF}
def sig(r): n = r.get("near", 0); return 0.25 if n >= 0.3 else 0.5 if n >= 0.1 else 2.0
idx = sorted(C); i0, i1 = idx[0], idx[-1]
dt = 1 / S["fps"]; F = np.eye(6); F[:3, 3:] = dt * np.eye(3); G = np.r_[0.5*dt*dt*np.eye(3), dt*np.eye(3)]; Q = Q_ACC**2 * G @ G.T
H = np.c_[np.eye(3), np.zeros((3, 3))]
def smooth(meas):
    x = np.r_[meas[min(meas)][0], 0, 0, 0]; P = np.diag([1.0]*3 + [400]*3); xs, Ps, xp, Pp = [], [], [], []
    for i in range(i0, i1 + 1):
        if i > i0: x = F @ x; P = F @ P @ F.T + Q
        xp.append(x.copy()); Pp.append(P.copy())
        if i in meas:
            z, s = meas[i]; K = P @ H.T @ np.linalg.inv(H @ P @ H.T + s*s*np.eye(3)); x = x + K @ (z - H @ x); P = (np.eye(6) - K @ H) @ P
        xs.append(x.copy()); Ps.append(P.copy())
    out = [None] * len(xs); out[-1] = xs[-1]
    for k in range(len(xs) - 2, -1, -1):
        Cg = Ps[k] @ F.T @ np.linalg.inv(Pp[k+1]); out[k] = xs[k] + Cg @ (out[k+1] - xp[k+1])
    return {i0 + k: v[:3] for k, v in enumerate(out)}
meas = {i: (np.array(C[i]["pos"]), sig(C[i])) for i in idx}
sm = smooth(meas)
kept = {i: m for i, m in meas.items() if np.linalg.norm(m[0] - sm[i]) < 3 * m[1] + 0.5}
sm = smooth(kept)
# rotation: drop a CPR rotation that disagrees with the slerp of its nearest CPR neighbours (unchecked themselves;
# the span ends and frames beside a gap over 12 frames are kept without the test)
rot_ok = []
for k, i in enumerate(idx):
    a, b = (idx[k-1] if k else None), (idx[k+1] if k + 1 < len(idx) else None)
    if a is None or b is None or b - a > 12: rot_ok.append(i); continue
    ra, rb = Rot.from_quat(C[a]["quat"]), Rot.from_quat(C[b]["quat"])
    pred = Slerp([a, b], Rot.concatenate([ra, rb]))([i])[0]
    nb = np.degrees((ra.inv() * rb).magnitude())
    if np.degrees((pred.inv() * Rot.from_quat(C[i]["quat"])).magnitude()) < ROT_OUT + 0.5 * nb: rot_ok.append(i)
sl = Slerp(rot_ok, Rot.from_quat([C[i]["quat"] for i in rot_ok]))
out = []
for i, p in enumerate(S["poses"]):
    if p is None or i < TAKEOFF or i < i0 or i > i1: out.append(p); continue
    src = "cpr" if i in kept and i in rot_ok else "cpr-rot" if i in rot_ok else "cpr-fill"
    out.append({"i": i, "t": p["t"], "pos": np.round(sm[i], 3).tolist(), "quat": np.round(sl([i]).as_quat()[0], 6).tolist(), "src": src})
if ROT_SIGMA > 0:
    k_ = [k for k, o in enumerate(out) if o and o["src"].startswith("cpr")]; Rq = Rot.from_quat([out[k]["quat"] for k in k_]); w = int(3 * ROT_SIGMA)
    sm_q = [Rq[max(0, j - w):j + w + 1].mean(weights=np.exp(-0.5 * ((np.arange(max(0, j - w), min(len(k_), j + w + 1)) - j) / ROT_SIGMA) ** 2)).as_quat() for j in range(len(k_))]
    for j, k in enumerate(k_): out[k]["quat"] = np.round(sm_q[j], 6).tolist()
json.dump({**{k: v for k, v in S.items() if k != "poses"}, "poses": out}, open(sys.argv[3], "w"))
print(f"{len(C)} CPR frames >= {MIN_INL} inliers of {i1 - i0 + 1} in span; positions kept {len(kept)}, rotations kept {len(rot_ok)}; "
      f"sigma 0.25/0.5/2.0: {sum(1 for m in meas.values() if m[1]==0.25)}/{sum(1 for m in meas.values() if m[1]==0.5)}/{sum(1 for m in meas.values() if m[1]==2.0)}")
