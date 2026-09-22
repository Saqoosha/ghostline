"""Human correspondences -> pose measurements. marks.json (from the viewer's "mark" mode) holds
landmarks (flag bases, gate feet: things the wind does not move) and marks: a pixel on a DVR frame
tagged with a landmark. Two steps:
 1. each landmark's 3D position: least-squares intersection of the rays through its marks from the
    frames' current poses, with RANSAC over rays (a mark on a badly posed frame is an outlier). A
    landmark with a 'pos' already in marks.json keeps it.
 2. each marked frame: minimise the marks' reprojection over the pose, with a prior on the rotation
    (sigma 3 deg: the heading is the reliable part) and, with fewer than 2 marks, on the altitude
    (sigma 1 m: one ray only fixes the position up to depth). Frames whose marks already reproject
    within RES_OK px keep their pose.
Writes the solved poses into a copy of the track (dvr_track.json entries, label 'manual', inliers
1000 so resolve/interp60's weighting trusts them) and a landmarks update back into marks.json, so
the viewer draws where each landmark projects under any pose set.
usage: marks_solve.py marks.json poses.json track.json dvr_pinhole.mp4.json out_track.json"""
import json, sys, re, math, numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation as Rot
marks_path, poses_path, track_path, cam_path, out_path = sys.argv[1:6]
M = json.load(open(marks_path)); P = {p["i"]: p for p in json.load(open(poses_path))["poses"] if p}
cam = json.load(open(cam_path)); K = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
RES_OK, SIG_ROT, SIG_ALT, SIG_POS, MAX_MOVE = 6.0, math.radians(3.0), 0.7, 2.5, 8.0
# One mark fixes the pose up to the depth along its ray; the altitude prior and a loose prior on the
# init position pick the point on the ray. Two or more marks pin the position; those frames get the
# strong weight. Unbounded, a near-horizontal ray let the solver walk 10^8 m (#1610-#1638, 2026-09-22).
def Rw(p): return Rot.from_quat(p["quat"]).as_matrix()          # world-from-camera, web frame
def ray(p, u, v):
    d = Rw(p) @ np.array([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], 1.0]); return np.array(p["pos"]), d / np.linalg.norm(d)
def project(Rc, C, X):
    d = Rc.T @ (X - C); return np.array([K[0, 0] * d[0] / d[2] + K[0, 2], K[1, 1] * d[1] / d[2] + K[1, 2]]) if d[2] > 0.1 else np.array([1e4, 1e4])
def intersect(rays):                                            # least-squares point closest to all rays
    A = np.zeros((3, 3)); b = np.zeros(3)
    for o, d in rays: Pm = np.eye(3) - np.outer(d, d); A += Pm; b += Pm @ o
    return np.linalg.solve(A, b)
by_lm = {}
for m in M["marks"]:
    if m["i"] in P: by_lm.setdefault(m["id"], []).append(m)
# 1. landmarks
for lm, ms in by_lm.items():
    if M["landmarks"].get(lm, {}).get("pos"): continue
    rays = [(ray(P[m["i"]], m["u"], m["v"]), m["i"]) for m in ms]
    if len(rays) < 2: print(f"landmark {lm}: {len(rays)} mark(s) only, cannot triangulate (mark it on a second, well-posed frame)"); continue
    best = None
    for k in range(len(rays)):                                  # RANSAC on pairs, then refit on inliers
        for l in range(k + 1, len(rays)):
            X = intersect([rays[k][0], rays[l][0]])
            inl = [r for r in rays if np.linalg.norm(np.cross(r[0][1], X - r[0][0])) < 1.0]
            if best is None or len(inl) > len(best): best = inl
    X = intersect([r[0] for r in best])
    dist = [round(float(np.linalg.norm(np.cross(r[0][1], X - r[0][0]))), 2) for r in rays]
    M["landmarks"][lm]["pos"] = np.round(X, 3).tolist()
    print(f"landmark {lm} ({M['landmarks'][lm].get('name','')}): {len(best)}/{len(rays)} rays agree, pos {np.round(X, 2).tolist()}, ray distances {dist}")
json.dump(M, open(marks_path, "w"), indent=1)
# 2. frames
Mz = np.diag([1.0, 1.0, -1.0]); solved = {}
by_frame = {}
for m in M["marks"]:
    if m["i"] in P and M["landmarks"].get(m["id"], {}).get("pos"): by_frame.setdefault(m["i"], []).append(m)
for i, ms in sorted(by_frame.items()):
    p = P[i]; R0, C0 = Rw(p), np.array(p["pos"]); X = [np.array(M["landmarks"][m["id"]]["pos"]) for m in ms]; uv = [np.array([m["u"], m["v"]]) for m in ms]
    res0 = [float(np.linalg.norm(project(R0, C0, x) - u)) for x, u in zip(X, uv)]
    if max(res0) <= RES_OK: print(f"#{i}: {len(ms)} mark(s) already within {max(res0):.1f} px, kept"); continue
    def f(x):
        Rc = Rot.from_rotvec(x[:3]).as_matrix() @ R0; C = C0 + x[3:]
        r = [(project(Rc, C, xx) - u) / 2.0 for xx, u in zip(X, uv)]      # px / 2 px
        r.append(x[:3] / SIG_ROT)
        if len(ms) < 2: r.append(np.array([x[4] / SIG_ALT])); r.append(x[3:] / SIG_POS)
        return np.concatenate(r)
    # With two or more marks the rotation is observable, so the bound opens to 90 deg and the start
    # is the yaw (about world up) that best explains the marks: a frame in a 500 deg/s turn can have
    # its init pose facing away from the landmarks (residual 1e4 px, a flat start for least squares).
    # Two marks do not pin a 6-DoF pose (the camera can slide along a curve: #1624 went 16 m with
    # 1.6 px residual), so the wide rotation and the yaw search need three or more; two marks get
    # a 30 deg bound, one mark 15 deg.
    x0 = np.zeros(6); rot_lim = math.radians(15 if len(ms) < 2 else 30)
    if len(ms) >= 3:
        rot_lim = math.pi; best = (max(res0), 0.0)
        for yaw in np.arange(-180, 180, 5.0):
            rv = Rot.from_rotvec(np.array([0, 1.0, 0]) * math.radians(yaw)); Rc = rv.as_matrix() @ R0
            r = max(float(np.linalg.norm(project(Rc, C0, x) - u)) for x, u in zip(X, uv))
            if r < best[0]: best = (r, yaw)
        x0[:3] = Rot.from_rotvec(np.array([0, 1.0, 0]) * math.radians(best[1])).as_rotvec()
    move_lim = MAX_MOVE if len(ms) < 3 else 15.0                  # three marks pin the position; the init may be that far off
    lim = np.r_[[rot_lim] * 3, [move_lim] * 3]; x0 = np.clip(x0, -lim * 0.99, lim * 0.99)
    sol = least_squares(f, x0, method="trf", bounds=(-lim, lim))
    Rc = Rot.from_rotvec(sol.x[:3]).as_matrix() @ R0; C = C0 + sol.x[3:]
    res1 = [float(np.linalg.norm(project(Rc, C, x) - u)) for x, u in zip(X, uv)]
    mv = float(np.linalg.norm(sol.x[3:]))
    res_ok = 10.0 if len(ms) < 3 else 25.0                        # several hand clicks disagree by a few px each
    if max(res1) > res_ok or mv >= move_lim - 0.01:
        print(f"#{i}: {len(ms)} mark(s), residual {max(res0):.0f} -> {max(res1):.1f} px, moved {mv:.2f} m: NOT solvable from this mark alone, skipped"); continue
    print(f"#{i}: {len(ms)} mark(s), residual {max(res0):.0f} -> {max(res1):.1f} px, moved {mv:.2f} m, turned {math.degrees(Rot.from_matrix(Rc @ R0.T).magnitude()):.1f} deg")
    solved[i] = (Rc, C, len(ms))
# 3. write into the track: replace the frame's entry (any batch dir) or add one
T = json.load(open(track_path)); byi = {}
for f_ in T: byi.setdefault(int(re.search(r"dvr_(\d+)", f_["name"]).group(1)), []).append(f_)
for i, (Rc, C, nm) in solved.items():
    e = {"t": round(80 + i / 60, 4), "name": f"manual/dvr_{i:05d}.jpg", "label": "manual", "pos": (C @ Mz).tolist(), "fwd": (Mz @ Rc @ np.array([0, 0, 1.0])).tolist(), "R": (Mz @ Rc).tolist(), "orig": (C @ Mz).tolist(), "inliers": 1000 if nm >= 2 else 100}
    for old in byi.get(i, []): old["label"] = "replaced-by-manual"; old["pos"] = None
    T.append(e)
json.dump(T, open(out_path, "w"))
print(f"{len(solved)} frames solved from marks -> {out_path}")
