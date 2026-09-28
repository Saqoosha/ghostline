"""Relative motion between neighbouring DVR frames, from the DVR alone: which way the camera travelled.
Where the scan match cannot place a frame (k ~ 1: the answer only echoes the render's viewpoint, KNT #1352-#1364, far
trees and a blurred grass foreground) the path there was drawn by the motion prior alone. The near grass that defeats
the scan match is what moves most between two frames, so the direction of travel is measured best exactly there.
For each pair (i, i + STEP): corners tracked with pyramidal LK both ways, an essential matrix by RANSAC, recoverPose.
Written per pair: the rotation from frame i's camera to frame j's, the unit direction of travel in frame i's camera
axes (x right, y down, z forward), the inlier count, and the parallax: the median inlier flow left after the measured
rotation is taken out, in pixels. With little parallax the direction is not measured (a pure turn), and cpr_ba.py
weights each pair by it.
Measured, it does not earn a place in the chain yet. On d05 (60 fps goggle DVR) the travel agreed with the solved
track to p50 1.9 / p90 5.4 deg over 20 px of parallax, and cpr_ba.py FLOW= still moved nothing that mattered (blackbox
accel residual 9.5/9.9/12.3 -> 9.3/9.8/12.4 m/s^2, far >10 px 24 -> 26, snap gyro p50 203 -> 270 deg/s): where the
scan match places the frames the flow adds nothing. On KNT's 30 fps race feed, the stretch it was built for
(#1352-#1364), it had 60-250 points and 3-10 px of parallax a pair and swung 80-120 deg frame to frame; the path moved 1 cm.
usage: flow_rel.py dvr_pinhole.mp4 out.jsonl   env: STEP (frames, default: fps / 30 rounded), CLEAN (hdz_blocks.py dir:
its painted video and masks), MIN_INL (60), ROT (a solved poses.json: its rotations, travel only)"""
import json, os, sys
import cv2, numpy as np
video, out = sys.argv[1:3]
cam = json.load(open(video + ".json")); W, H, fps = cam["width"], cam["height"], cam["fps"]
K = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
STEP = int(os.environ.get("STEP", max(1, round(fps / 30)))); MIN_INL = int(os.environ.get("MIN_INL", 60))
CLEAN = os.environ.get("CLEAN")
masks = np.load(f"{CLEAN}/noise_masks.npz")["masks"] if CLEAN else None
# where the picture is valid: not the OSD bands (as cpr_track.py) and not the black rim the undistortion leaves
fk = cam["source_fisheye"]; Kf = np.array([[fk["fx"], 0, fk["cx"]], [0, fk["fy"], fk["cy"]], [0, 0, 1]])
m1, m2 = cv2.fisheye.initUndistortRectifyMap(Kf, np.array(fk["k"]), np.eye(3), K, (W, H), cv2.CV_16SC2)
osd = np.full((H, W), 255, np.uint8); osd[:82] = 0; osd[632:] = 0
valid = cv2.erode(cv2.remap(osd, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0), np.ones((15, 15), np.uint8))
cap = cv2.VideoCapture(f"{CLEAN}/dvr_pinhole_clean.mp4" if CLEAN else video)
if not cap.isOpened(): sys.exit(f"cannot read {CLEAN}/dvr_pinhole_clean.mp4" if CLEAN else f"cannot read {video}")
buf, i, n_ok = {}, 0, 0
lk = dict(winSize=(21, 21), maxLevel=4, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
# ROT=<poses.json>: take the rotation between the two frames from a solved track instead of from the essential matrix.
# The 5-point solve gets rotation and travel together, and at a 30 fps race feed (KNT: 20+ deg a frame, blurred) the
# travel came out wrong: against the solved track p50 8-16 deg, p90 60-129 deg (flipped). The track's rotation agrees
# with the flow's to 0.65 deg p50, so with it known the travel t is the null vector of (R x0) x x1 over the points:
# two points fix it, RANSAC on pairs, and the sign is the one that puts the points in front of both cameras.
ROTS = None
if os.environ.get("ROT"):
    from scipy.spatial.transform import Rotation as Rot
    ROTS = [Rot.from_quat(p["quat"]) if p else None for p in json.load(open(os.environ["ROT"]))["poses"]]
Kinv = np.linalg.inv(K); rng = np.random.default_rng(0); THR = float(os.environ.get("THR", 4.0))
def known_rotation(a, j, x0, x1):
    if ROTS[a] is None or ROTS[j] is None: return None
    Rr = (ROTS[j].inv() * ROTS[a]).as_matrix()               # frame a's camera axes -> frame j's
    b0 = (Kinv @ np.c_[x0, np.ones(len(x0))].T).T; b1 = (Kinv @ np.c_[x1, np.ones(len(x1))].T).T
    rb0 = (Rr @ b0.T).T; c = np.cross(rb0, b1)                # c . t = 0 for the true t (in frame j's axes)
    # residual in pixels: derotated, every flow line passes through the epipole K t; distance of x1 from the line
    # through x0's derotated position and that epipole (homogeneous, so an epipole at infinity - sideways - works too)
    xw = (K @ rb0.T).T; xw = xw / xw[:, 2:]; x1h = np.c_[x1, np.ones(len(x1))]
    def pxres(t):
        l = np.cross(xw, (K @ t)[None]); return np.abs(np.sum(l * x1h, axis=1)) / np.maximum(np.linalg.norm(l[:, :2], axis=1), 1e-12)
    best, bestm = None, None
    for _ in range(300):
        u, v = rng.choice(len(c), 2, replace=False); t = np.cross(c[u], c[v]); nt = np.linalg.norm(t)
        if nt < 1e-12: continue
        m = pxres(t / nt) < THR
        if bestm is None or m.sum() > bestm.sum(): best, bestm = t / nt, m
    if bestm is None or bestm.sum() < MIN_INL: return None
    _, _, Vt = np.linalg.svd(c[bestm]); t = Vt[-1]
    # sign: depths along the rays must come out positive (z1 b1 = z0 R b0 + t); count points agreeing with each sign
    A = np.stack([rb0[bestm], -b1[bestm]], axis=2)                   # [R b0, -b1] [z0 z1]^T = -t
    z = np.array([np.linalg.lstsq(A[k], -t, rcond=None)[0] for k in range(len(A))])
    if np.sum((z > 0).all(1)) < np.sum((z < 0).all(1)): t = -t
    d = -Rr.T @ t                                               # travel, in frame a's axes
    h = (K @ Rr @ Kinv @ np.c_[x0[bestm], np.ones(bestm.sum())].T).T; h = h[:, :2] / h[:, 2:]
    par = float(np.median(np.linalg.norm(x1[bestm] - h, axis=1)))
    return dict(i=a, j=j, rotvec=np.round(cv2.Rodrigues(Rr)[0][:, 0], 6).tolist(), dir=np.round(d / np.linalg.norm(d), 5).tolist(), inliers=int(bestm.sum()), parallax=round(par, 2), rot="track")
f = open(out, "w")
def mask_for(k):
    m = valid.copy()
    if masks is not None and k < len(masks): m[cv2.resize(masks[k].astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0] = 0
    return m
while True:
    ok, fr = cap.read()
    if not ok: break
    buf[i] = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY); buf.pop(i - STEP - 1, None)
    a = i - STEP
    if a in buf:
        g0, g1 = buf[a], buf[i]
        rec = None
        # with the rotation known, frame a is first warped by it onto frame j's view: what is left to track is the
        # parallax alone. Unwarped, KNT moved 30-110 px a frame and 1-2 in 10 tracks survived the round trip (#1348-#1364)
        Hw = None
        if ROTS is not None and ROTS[a] is not None and ROTS[i] is not None:
            Hw = K @ (ROTS[i].inv() * ROTS[a]).as_matrix() @ Kinv
            g0 = cv2.warpPerspective(g0, Hw, (W, H)); m0 = cv2.warpPerspective(mask_for(a), Hw, (W, H), flags=cv2.INTER_NEAREST)
        else: m0 = mask_for(a)
        p0 = cv2.goodFeaturesToTrack(g0, 2000, 0.005, 7, mask=cv2.erode(m0, np.ones((9, 9), np.uint8)))
        if p0 is not None and len(p0) >= MIN_INL:
            p1, s1, _ = cv2.calcOpticalFlowPyrLK(g0, g1, p0, None, **lk)
            pb, s2, _ = cv2.calcOpticalFlowPyrLK(g1, g0, p1, None, **lk)
            good = (s1[:, 0] == 1) & (s2[:, 0] == 1) & (np.linalg.norm(pb - p0, axis=2)[:, 0] < 0.7)
            x0, x1 = p0[good, 0], p1[good, 0]
            inb = (x1[:, 0] >= 0) & (x1[:, 0] < W) & (x1[:, 1] >= 0) & (x1[:, 1] < H)
            x0, x1 = x0[inb], x1[inb]
            if Hw is not None:                                  # back to frame a's own pixels
                h = (np.linalg.inv(Hw) @ np.c_[x0, np.ones(len(x0))].T).T; x0 = (h[:, :2] / h[:, 2:]).astype(np.float32)
            if len(x0) >= MIN_INL and ROTS is not None:
                rec = known_rotation(a, i, x0, x1)
            elif len(x0) >= MIN_INL:
                E, em = cv2.findEssentialMat(x0, x1, K, cv2.RANSAC, 0.999, 1.0)
                if E is not None and E.shape == (3, 3):
                    n, Rr, t, pm = cv2.recoverPose(E, x0, x1, K, mask=em.copy())
                    inl = pm[:, 0] > 0
                    if inl.sum() >= MIN_INL:
                        # x_j = Rr x_i + t: frame j's centre in frame i's axes is -Rr^T t, so that is the travel
                        d = -Rr.T @ t[:, 0]; d /= np.linalg.norm(d)
                        # parallax: where frame i's points land under the rotation alone, against where they went
                        h = (K @ Rr @ np.linalg.inv(K) @ np.c_[x0[inl], np.ones(inl.sum())].T).T; h = h[:, :2] / h[:, 2:]
                        par = float(np.median(np.linalg.norm(x1[inl] - h, axis=1)))
                        q = cv2.Rodrigues(Rr)[0][:, 0]
                        rec = dict(i=a, j=i, rotvec=np.round(q, 6).tolist(), dir=np.round(d, 5).tolist(), inliers=int(inl.sum()), parallax=round(par, 2))
        if rec: f.write(json.dumps(rec) + "\n"); n_ok += 1
    i += 1
f.close()
print(f"{n_ok} of {i - STEP} pairs (step {STEP}) written to {out}")
