"""Every DVR frame at 60 fps: a constant-velocity Kalman filter + RTS smoother run ON THE 60 fps
GRID, with the kept 10 fps frames as measurements. Between measurements the state coasts, so a
gap across a hairpin becomes a straight-ish line, never a loop (cubic Hermite through the two
end velocities drew a loop there). Orientation: slerp between kept frames.
Output frame: web (x east, y up, z south), metres, right-handed."""
import json, sys, numpy as np
from scipy.spatial.transform import Rotation as Rot, Slerp
d = [x for x in json.load(open(sys.argv[1])) if x["pos"] is not None]
# An LK frame whose PnP returned the previous LK frame's position (< 2 cm apart while the drone
# moves ~0.3 m/frame) is a stale track, not a measurement: 14 of 2050 at 2026-09-21, in runs of
# up to 4 (#184-#187), and each one pulled the smoother toward a point the drone had left.
keep, prev = [], None
for x in d:
    lk = x["name"].startswith("lk/")
    if lk and prev is not None and np.linalg.norm(np.array(x["pos"]) - prev) < 0.02: continue
    prev = np.array(x["pos"]) if lk else None; keep.append(x)
d = keep
Mz = np.diag([1.0, 1.0, -1.0])
tm = np.array([x["t"] for x in d]); Z = np.array([x["pos"] for x in d]) @ Mz
# Orientation only from frames COLMAP registered: an LK-tracked frame's PnP on a few ground
# points barely constrains rotation and keeps its anchor's heading, which shows as staircase
# jumps of 30-50 deg in a fast turn (106.7 s). Positions from every kept frame. An LK frame is
# labelled "lk", not "kept": its slerped heading and few-point PnP are worth refining (#184 was
# 3 deg of yaw and 0.4 m off while run 3 skipped it as observed).
rd = [x for x in d if not x["name"].startswith("lk/")]
tr = np.array([x["t"] for x in rd]); Rs = Rot.from_matrix(np.array([Mz @ np.array(x["R"]) for x in rd])); sl = Slerp(tr, Rs)
T0, FPS, N = 80.0, 60, int(sys.argv[3]); q, r = 40.0, 0.4          # accel [m/s^2], measurement sigma [m]
TAKEOFF = 116                                                        # the drone sits on the ground until this frame (Saqoosha, from the DVR)
tg = T0 + np.arange(N) / FPS
meas = {}                                                            # grid index -> measurement
for t_, z in zip(tm, Z): meas.setdefault(int(round((t_ - T0) * FPS)), []).append(z)
col = {int(round((x["t"] - T0) * FPS)) for x in rd}                  # grid indices COLMAP registered
H = np.c_[np.eye(3), np.zeros((3, 3))]; dt = 1 / FPS
F = np.eye(6); F[:3, 3:] = dt * np.eye(3); G = np.r_[0.5*dt*dt*np.eye(3), dt*np.eye(3)]; Q = q*q * G @ G.T
i0, i1 = min(meas), max(meas)
x = np.r_[np.mean(meas[i0], axis=0), 0, 0, 0]; P = np.diag([r*r]*3 + [400]*3)
xs, Ps, xp, Pp = [], [], [], []
for i in range(i0, i1 + 1):
    if i > i0: x = F @ x; P = F @ P @ F.T + Q
    xp.append(x.copy()); Pp.append(P.copy())
    for z in meas.get(i, []):
        S_ = H @ P @ H.T + r*r*np.eye(3); K = P @ H.T @ np.linalg.inv(S_); x = x + K @ (z - H @ x); P = (np.eye(6) - K @ H) @ P
    xs.append(x.copy()); Ps.append(P.copy())
xsm = [None] * len(xs); xsm[-1] = xs[-1]
for k in range(len(xs) - 2, -1, -1):
    C = Ps[k] @ F.T @ np.linalg.inv(Pp[k+1]); xsm[k] = xs[k] + C @ (xsm[k+1] - xp[k+1])
# Ground: every observation before take-off is the same pose, so average them (position: mean of
# all measurements; rotation: chordal mean of the COLMAP frames) and give it to every frame there.
gpos = np.median([z for i in meas if i < TAKEOFF for z in meas[i]], axis=0)     # median: one PnP sat 1.8 m out
gq = np.array([x.as_quat() for x, t_ in zip(Rs, tr) if int(round((t_ - T0) * FPS)) < TAKEOFF]); gq[gq[:, 3] < 0] *= -1
gquat = Rot.from_quat(gq.mean(axis=0) / np.linalg.norm(gq.mean(axis=0))).as_quat()
print(f"ground: frames {i0}-{TAKEOFF-1}, {sum(len(meas[i]) for i in meas if i < TAKEOFF)} observations, spread p50 {np.median(np.linalg.norm([z for i in meas if i < TAKEOFF for z in meas[i]] - gpos, axis=1)):.2f} max {np.linalg.norm([z for i in meas if i < TAKEOFF for z in meas[i]] - gpos, axis=1).max():.2f} m")
out = [None] * N
for k, i in enumerate(range(i0, i1 + 1)):
    tt = tg[i]; qv = sl([min(max(tt, tr[0]), tr[-1])]).as_quat()[0]
    out[i] = {"i": i, "t": round(float(tt), 4), "pos": np.round(xsm[k][:3], 3).tolist(), "quat": np.round(qv, 6).tolist(), "src": "kept" if i in col else "lk" if i in meas else "interp"}
    if i < TAKEOFF: out[i].update(pos=np.round(gpos, 3).tolist(), quat=np.round(gquat, 6).tolist(), src="ground")
json.dump({"frame": "web: x east, y up, z south, metres, right-handed; quat (x,y,z,w) is world-from-camera with COLMAP camera axes (looks +z, y down)", "t0": T0, "fps": FPS, "poses": out}, open(sys.argv[2], "w"))
have = [o for o in out if o]; v = np.linalg.norm([xsm[k][3:] for k in range(len(xsm))], axis=1) * 3.6
print(f"{N} frames, {len(have)} inside the tracked span ({have[0]['t']:.2f}-{have[-1]['t']:.2f}s), {sum(1 for o in have if o['src']=='kept')} anchored, {sum(1 for o in have if o['src']=='lk')} lk | speed p50 {np.median(v):.0f} max {v.max():.0f} km/h")
