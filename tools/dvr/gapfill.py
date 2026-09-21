"""Fill gaps in the DVR track by tracking, not matching: from each kept frame at a gap's edge, take
its 2D points that have a 3D point in the scan model, follow them frame by frame with Lucas-Kanade
optical flow (survives the motion blur that kills SIFT), and solve PnP (fisheye) on every frame.
Both edges track inward. Output: extra pose lines in COLMAP images.txt format (model frame) plus
an inlier count, to be merged into dvr_poses_all.txt / dvr_inliers.txt for resolve.py."""
import sys, re, json, numpy as np, cv2
from scipy.spatial.transform import Rotation as Rot
model, video, track_json, out_prefix = sys.argv[1:5]
FPS, T0, MIN_PTS, MAX_GAP_S = 60, 80.0, 15, 1.6
cam = [l.split() for l in open(f"{model}/cameras.txt") if "FISHEYE" in l][0]; p = np.array(cam[4:], float)
K = np.array([[p[0], 0, p[2]], [0, p[1], p[3]], [0, 0, 1]]); D = p[4:8]
X = {}
for l in open(f"{model}/points3D.txt"):
    if l[0] != "#": f = l.split(); X[int(f[0])] = np.array([float(f[1]), float(f[2]), float(f[3])])
obs = {}                                              # frame index -> (uv Nx2, xyz Nx3)
L = [l for l in open(f"{model}/dvr_images.txt")]
for a, b in zip(L[0::2], L[1::2]):
    f = a.split(); i = int(re.search(r"dvr_(\d+)", f[9]).group(1)); pts = b.split(); uv, xyz = [], []
    for k in range(0, len(pts), 3):
        pid = int(pts[k+2])
        if pid != -1 and pid in X: uv.append((float(pts[k]), float(pts[k+1]))); xyz.append(X[pid])
    if len(uv) >= MIN_PTS and (i not in obs or len(uv) > len(obs[i][0])): obs[i] = (np.array(uv, np.float32), np.array(xyz))
track = json.load(open(track_json)); kept = sorted(int(round((x["t"] - T0) * FPS)) for x in track if x["pos"] is not None and x["label"] not in ("outlier",))
gaps = [(a, b) for a, b in zip(kept, kept[1:]) if 2 < b - a <= MAX_GAP_S * FPS]
print(f"{len(obs)} frames with 2D-3D matches, {len(gaps)} gaps to fill ({sum(b-a-1 for a,b in gaps)} frames)")
cap = cv2.VideoCapture(video); W, H = 960, 720
def frame(i):
    cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, fr = cap.read()
    if not ok: return None
    return cv2.cvtColor(fr[:, 160:160 + W], cv2.COLOR_BGR2GRAY)          # same crop as the JPEGs COLMAP saw
lk = dict(winSize=(31, 31), maxLevel=4, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
def pnp(uv, xyz):
    und = cv2.fisheye.undistortPoints(uv.reshape(-1, 1, 2).astype(np.float64), K, D).reshape(-1, 2)
    ok, rvec, tvec, inl = cv2.solvePnPRansac(xyz.reshape(-1, 1, 3), und.reshape(-1, 1, 2), np.eye(3), None, reprojectionError=0.004, iterationsCount=200, confidence=0.999, flags=cv2.SOLVEPNP_EPNP)
    if not ok or inl is None or len(inl) < 10: return None
    return rvec.ravel(), tvec.ravel(), len(inl)
def walk(i0, step, stop):
    """Track from kept frame i0 in direction step until frame `stop` (exclusive); yield (i, rvec, tvec, ninl)."""
    if i0 not in obs: return
    uv, xyz = obs[i0]; prev = frame(i0); out = []
    i = i0 + step
    while i != stop and prev is not None:
        cur = frame(i)
        if cur is None: break
        nxt, st, err = cv2.calcOpticalFlowPyrLK(prev, cur, uv.reshape(-1, 1, 2), None, **lk)
        back, st2, _ = cv2.calcOpticalFlowPyrLK(cur, prev, nxt, None, **lk)
        good = (st.ravel() == 1) & (st2.ravel() == 1) & (np.linalg.norm(back.reshape(-1, 2) - uv, axis=1) < 1.0)
        good &= (nxt[:, 0, 0] > 2) & (nxt[:, 0, 0] < W - 2) & (nxt[:, 0, 1] > 84) & (nxt[:, 0, 1] < 630)   # inside the image, off the OSD
        uv, xyz = nxt.reshape(-1, 2)[good], xyz[good]
        if len(uv) < MIN_PTS: break
        r = pnp(uv, xyz)
        if r is None: break
        out.append((i, r[0], r[1], r[2])); prev = cur; i += step
    return out
lines, inl, n_gap = [], [], 0
for a, b in gaps:
    fwd = {r[0]: r for r in (walk(a, +1, b) or [])}; bwd = {r[0]: r for r in (walk(b, -1, a) or [])}
    got = 0
    for i in range(a + 1, b):
        cands = [c for c in (fwd.get(i), bwd.get(i)) if c]
        if not cands: continue
        c = max(cands, key=lambda c: c[3]); rv, tv, n = c[1], c[2], c[3]
        if len(cands) == 2:                                 # both directions reached this frame: average the centres, keep the better rotation
            C = np.mean([-Rot.from_rotvec(k[1]).inv().apply(k[2]) for k in cands], axis=0); tv = -Rot.from_rotvec(rv).apply(C); n = sum(k[3] for k in cands)
        q = Rot.from_rotvec(rv).as_quat()                    # x,y,z,w -> COLMAP w,x,y,z
        lines.append(f"0 {q[3]} {q[0]} {q[1]} {q[2]} {tv[0]} {tv[1]} {tv[2]} 2 lk/dvr_{i:05d}.jpg"); inl.append(f"lk/dvr_{i:05d}.jpg {n}"); got += 1
    n_gap += got
    print(f"  gap {T0 + a/FPS:7.2f}-{T0 + b/FPS:7.2f}s ({b-a-1:3d} frames): filled {got}  (fwd {len(fwd)}, bwd {len(bwd)})", flush=True)
open(out_prefix + "_poses.txt", "w").write("\n".join(lines) + "\n"); open(out_prefix + "_inliers.txt", "w").write("\n".join(inl) + "\n")
print(f"filled {n_gap} frames")
