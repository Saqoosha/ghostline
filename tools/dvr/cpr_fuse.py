"""Fuse per-frame CPR poses into one 60 fps track (web frame).
Position: constant-velocity Kalman + RTS on the frame grid. A CPR position is only as good as its near
points (fast turns blur the near field away and the far points pin rotation, not position), so the
measurement sigma comes from the fraction of inliers within 15 m. Two passes: the second drops
measurements more than 3 sigma + 0.5 m from the first pass's smoothed track.
Rotation: the CPR rotation where it agrees with its neighbours, the short or long arc between (below), then a Gaussian of ROT_SIGMA
frames (weighted chordal mean). sigma 1 cut the frame-to-frame wobble 0.26 -> 0.09 deg and the mark residual
11.8 -> 11.2 px; sigma 3 lags the turns (14.4 px).
Frames before take-off and frames outside the CPR span keep the start pose.
usage: cpr_fuse.py start_poses.json cpr.jsonl out_poses.json   env: ROT_SIGMA (frames, default 1, 0 = off)"""
import json, sys, os, bisect, numpy as np
from scipy.spatial.transform import Rotation as Rot
MIN_INL, TAKEOFF, Q_ACC, ROT_OUT, GAP, DENSE = 100, 116, 40.0, 10.0, 12, 6   # TAKEOFF is hdz_0067's; Q_ACC as interp60.py
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
# rotation, gate: predict each CPR rotation from its neighbours and keep it if it agrees. First the dense stretches:
# neighbours 1 and 2 apart in the CPR list, all within DENSE frames, must both agree (two outliers in a row pass a
# 1-apart test by vouching for each other). Then outward, for every frame not yet kept: judge it against the
# extrapolation of the two nearest kept frames on each side (within GAP of it, within DENSE of each other). #3720-#3721,
# the two frames after a 20-frame gap, agreed with each other and jumped 93 deg in 4 frames to #3725; judged against
# #3725-#3726 they are dropped. A frame with kept rotations nearby but no pair to judge by is dropped too (round 3
# matches it again); only a frame with nothing kept within GAP is taken untested.
R = {i: Rot.from_quat(C[i]["quat"]) for i in idx}
def deviation(i, a, b):                              # degrees off the prediction from a, b (either side of i, or both on one side)
    step = (R[a].inv() * R[b]).as_rotvec() / (b - a)
    pred = R[a] * Rot.from_rotvec(step * (i - a)); return np.degrees((pred.inv() * R[i]).magnitude()), np.degrees(np.linalg.norm(step * abs(i - a)))
def agrees(i, pairs): return all(dev < ROT_OUT + 0.5 * span for dev, span in (deviation(i, a, b) for a, b in pairs))
ok, undecided, failed = set(), [], set()
for k, i in enumerate(idx):
    pairs = [(idx[k-d], idx[k+d]) for d in (1, 2) if k - d >= 0 and k + d < len(idx) and i - idx[k-d] <= DENSE and idx[k+d] - i <= DENSE]
    if len(pairs) == 2 and agrees(i, pairs): ok.add(i)
    else: undecided.append(i); (failed.add(i) if len(pairs) == 2 else None)
for _ in range(len(undecided)):                      # each pass decides the frames next to kept ones
    changed, kept_r = False, sorted(ok)
    for i in list(undecided):
        p = bisect.bisect_left(kept_r, i)
        left = [j for j in kept_r[max(0, p - 2):p][::-1] if i - j <= GAP]; right = [j for j in kept_r[p:p + 2] if j - i <= GAP]
        pairs = [(x[0], x[1]) for x in (left, right) if len(x) == 2 and abs(x[1] - x[0]) <= DENSE]
        if not pairs: continue
        undecided.remove(i); changed = True
        if agrees(i, pairs): ok.add(i)
    if not changed: break
kept_r = sorted(ok)
for i in undecided:                                  # never a frame the dense test failed
    if i in failed: continue
    p = bisect.bisect_left(kept_r, i)
    if not ((p and i - kept_r[p - 1] <= GAP) or (p < len(kept_r) and kept_r[p] - i <= GAP)): ok.add(i)
rot_ok = sorted(ok)
if len(rot_ok) < 2: sys.exit(f"cpr_fuse: only {len(rot_ok)} CPR rotations passed the gate")
# rotation, fill: between kept rotations take the arc (short, long, or with a whole extra turn) closest to the turn the
# angular rates on either side imply. Slerp always takes the short one, and #3700-#3720 (20 frames at 500-960 deg/s)
# came out turning 113 deg the wrong way instead of 247 deg the right way.
def rate(a, b): return (R[a].inv() * R[b]).as_rotvec() / (b - a)   # rad/frame, body frame
arc, n_long = {}, 0
for k in range(len(rot_ok) - 1):
    a, b = rot_ok[k], rot_ok[k+1]; v = (R[a].inv() * R[b]).as_rotvec()
    if b - a > 1:
        th = np.linalg.norm(v); side = [rate(rot_ok[k-1], a)] if k and a - rot_ok[k-1] <= DENSE else []
        if k + 2 < len(rot_ok) and rot_ok[k+2] - b <= DENSE: side.append(rate(b, rot_ok[k+2]))
        exp_v = np.mean(side, axis=0) * (b - a) if side else None
        ev = np.linalg.norm(exp_v) if exp_v is not None else 0
        u = None if exp_v is None else v / th if th > 0.2 else exp_v / ev if ev > 1e-6 else None   # near a whole turn: axis from the rates
        if u is not None:
            cand = [v + u * 2 * np.pi * t for t in (-2, -1, 0, 1)]         # t = -1 is the long arc
            best = min(cand, key=lambda c: np.linalg.norm(c - exp_v))
            if best is not cand[2]: v = best; n_long += 1
    arc[a] = (b, v)
def rot_at(i):
    if i <= rot_ok[0]: return R[rot_ok[0]]
    if i >= rot_ok[-1]: return R[rot_ok[-1]]
    a = rot_ok[np.searchsorted(rot_ok, i, side="right") - 1]; b, v = arc[a]
    return R[a] * Rot.from_rotvec(v * (i - a) / (b - a))
out = []
for i, p in enumerate(S["poses"]):
    if p is None or i < TAKEOFF or i < i0 or i > i1: out.append(p); continue
    src = "cpr" if i in kept and i in rot_ok else "cpr-rot" if i in rot_ok else "cpr-fill"
    out.append({"i": i, "t": p["t"], "pos": np.round(sm[i], 3).tolist(), "quat": np.round(rot_at(i).as_quat(), 6).tolist(), "src": src})
if ROT_SIGMA > 0:
    k_ = [k for k, o in enumerate(out) if o and o["src"].startswith("cpr")]; Rq = Rot.from_quat([out[k]["quat"] for k in k_]); w = int(3 * ROT_SIGMA)
    sm_q = [Rq[max(0, j - w):j + w + 1].mean(weights=np.exp(-0.5 * ((np.arange(max(0, j - w), min(len(k_), j + w + 1)) - j) / ROT_SIGMA) ** 2)).as_quat() for j in range(len(k_))]
    for j, k in enumerate(k_): out[k]["quat"] = np.round(sm_q[j], 6).tolist()
json.dump({**{k: v for k, v in S.items() if k != "poses"}, "poses": out}, open(sys.argv[3], "w"))
print(f"{len(C)} CPR frames >= {MIN_INL} inliers of {i1 - i0 + 1} in span; positions kept {len(kept)}, rotations kept {len(rot_ok)}, long arcs {n_long}; "
      f"sigma 0.25/0.5/2.0: {sum(1 for m in meas.values() if m[1]==0.25)}/{sum(1 for m in meas.values() if m[1]==0.5)}/{sum(1 for m in meas.values() if m[1]==2.0)}")
