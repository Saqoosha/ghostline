"""Orientation (and position) between COLMAP anchors from the DVR itself, with depth from the scan.
Monocular flow between adjacent DVR frames cannot separate rotation from translation over a near
ground (0.3 m/frame at 10 m looks like 1-3 deg of rotation: dvr_gyro.py's essential matrix and
rotation-only homography both drifted 5 deg/frame). So: render the 3DGS depth at the current
chained pose, back-project the tracked points of frame i, solvePnP them against their positions in
frame i+1 -> full relative pose with scale. Chain from the anchor before the gap, measure the
closure against the anchor after, spread it. Adjacent DVR frames track well (same blur, exposure);
the render is only asked for depth, never for matching.
usage: gyro_pnp.py ply video init.json out.json [gap_min]   env: CLOSE_MAX (deg, 20), POS (1: also chain positions)"""
import json, sys, time, math, os, numpy as np, torch, cv2
from plyfile import PlyData
from gsplat import rasterization
from scipy.spatial.transform import Rotation as Rot
dev = "cuda"; ply, video, init_json, out_json = sys.argv[1:5]; GAP_MIN = int(sys.argv[5]) if len(sys.argv) > 5 else 2
CLOSE_MAX, POS, MIN_INL = float(os.environ.get("CLOSE_MAX", 20)), os.environ.get("POS", "1") == "1", int(os.environ.get("MIN_INL", 12))
SPAN = [int(x) for x in os.environ["SPAN"].split(",")] if os.environ.get("SPAN") else None   # a,b: chain a->b only, report, no output
SH, OPMIN, SCALE = 1, 0.1, 0.5
v = PlyData.read(ply)["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= OPMIN]
means = torch.tensor(np.c_[v["x"], v["y"], v["z"]], dtype=torch.float32, device=dev)
quats = torch.tensor(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], dtype=torch.float32, device=dev)
scales = torch.exp(torch.tensor(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]], dtype=torch.float32, device=dev))
opac = torch.sigmoid(torch.tensor(v["opacity"], dtype=torch.float32, device=dev))
sh0 = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :]
rest = np.stack([np.c_[[v[f"f_rest_{c*15+k}"] for k in range(15)]].T for c in range(3)], axis=2)
colors = torch.tensor(np.concatenate([sh0, rest], axis=1)[:, :(SH + 1) ** 2], dtype=torch.float32, device=dev)
cam = json.load(open(video + ".json")); W, H = cam["width"], cam["height"]
K = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
fk = cam["source_fisheye"]; Kf = np.array([[fk["fx"], 0, fk["cx"]], [0, fk["fy"], fk["cy"]], [0, 0, 1]]); Df = np.array(fk["k"])
m1, m2 = cv2.fisheye.initUndistortRectifyMap(Kf, Df, np.eye(3), K, (W, H), cv2.CV_16SC2)
osd = np.full((H, W), 255, np.uint8); osd[:82] = 0; osd[632:] = 0
valid = cv2.erode(cv2.remap(osd, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0), np.ones((15, 15), np.uint8))
w, h = int(W * SCALE), int(H * SCALE); Ks = K.copy(); Ks[:2] *= SCALE; Kt = torch.tensor(Ks, dtype=torch.float32, device=dev)[None]
def depth_at(Rw, C):                                 # depth + alpha at the fine level for world-from-camera (Rw, C)
    w2c = np.eye(4); w2c[:3, :3] = Rw.T; w2c[:3, 3] = -Rw.T @ C
    with torch.no_grad():
        img, alpha, _ = rasterization(means, quats, scales, opac, colors, torch.tensor(w2c, dtype=torch.float32, device=dev)[None], Kt, w, h, sh_degree=SH, render_mode="RGB+ED")
    return img[0, ..., 3].cpu().numpy(), alpha[0, ..., 0].cpu().numpy()
P = json.load(open(init_json)); poses = P["poses"]
anchors = [i for i, p in enumerate(poses) if p and p["src"] in ("kept", "ground")]
gaps = [(a, b) for a, b in zip(anchors, anchors[1:]) if b - a >= GAP_MIN]
if SPAN: gaps = [tuple(SPAN)]
print(f"{len(gaps)} gaps, {sum(b - a - 1 for a, b in gaps)} frames", flush=True)
cap = cv2.VideoCapture(video); at = 0; cache = {}
def gray(i):
    global at
    if i in cache: return cache[i]
    if i < at: cap.set(cv2.CAP_PROP_POS_FRAMES, i); at = i
    while at < i: cap.grab(); at += 1
    ok, fr = cap.read(); at += 1
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY); cache[i] = g
    if len(cache) > 6: cache.pop(min(cache))
    return g
lk = dict(winSize=(41, 41), maxLevel=5, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
def step(Rw, C, i):
    """pose of frame i+1 given pose (Rw, C) of frame i: track i->i+1, depth from the render at (Rw, C)"""
    g0, g1 = gray(i), gray(i + 1)
    pts = cv2.goodFeaturesToTrack(g0, 3000, 0.005, 6, mask=valid, blockSize=7)
    if pts is None or len(pts) < MIN_INL: return None, 0
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(g0, g1, pts, None, **lk); back, st2, _ = cv2.calcOpticalFlowPyrLK(g1, g0, nxt, None, **lk)
    good = (st[:, 0] == 1) & (st2[:, 0] == 1) & (np.linalg.norm((back - pts)[:, 0], axis=1) < 1.5)
    if good.sum() < MIN_INL: return None, int(good.sum())
    p0, p1 = pts[good, 0], nxt[good, 0]
    depth, alpha = depth_at(Rw, C)
    u, vv = np.round(p0[:, 0] * SCALE).astype(int).clip(0, w - 1), np.round(p0[:, 1] * SCALE).astype(int).clip(0, h - 1)
    z = depth[vv, u]; ok = (alpha[vv, u] > 0.9) & (z > 0.5) & (z < 300)
    if ok.sum() < MIN_INL: return None, int(ok.sum())
    p0, p1, z = p0[ok], p1[ok], z[ok]
    xc = (p0[:, 0] - K[0, 2]) / K[0, 0] * z; yc = (p0[:, 1] - K[1, 2]) / K[1, 1] * z
    X = (Rw @ np.stack([xc, yc, z], 1).T).T + C
    rv0, _ = cv2.Rodrigues(Rw.T); tv0 = (-Rw.T @ C).reshape(3, 1)
    okp, rvec, tvec, inl = cv2.solvePnPRansac(X.astype(np.float64), p1.astype(np.float64), K, None, rvec=rv0, tvec=tv0, useExtrinsicGuess=True, iterationsCount=200, reprojectionError=2.0, confidence=0.999, flags=cv2.SOLVEPNP_ITERATIVE)
    if not okp or inl is None or len(inl) < MIN_INL: return None, 0 if inl is None else int(len(inl))
    Rn, _ = cv2.Rodrigues(rvec); return (Rn.T, (-Rn.T @ tvec).ravel()), int(len(inl))
def R_of(p): return Rot.from_quat(p["quat"]).as_matrix()
out = [dict(p) if p else None for p in poses]; report = []; t0 = time.time(); nf = 0
for gi, (a, b) in enumerate(gaps):
    Rw, C = R_of(poses[a]), np.array(poses[a]["pos"], float); chain = [(Rw, C)]; inls = []; ok = True
    for i in range(a, b):
        r, n = step(Rw, C, i); inls.append(n)
        if r is None: ok = False; break
        Rw, C = r; chain.append((Rw, C))
    nf += b - a
    if SPAN: print(f"span #{a}-#{b}: per-step inliers {inls}", flush=True)
    if not ok: report.append((a, b, None, None, min(inls))); continue
    Rb, Cb = R_of(poses[b]), np.array(poses[b]["pos"], float)
    if SPAN:                                        # write the raw chain (no closure spread) for every frame of the span
        span_out = [None] * len(poses)
        for k, i in enumerate(range(a, b + 1)):
            span_out[i] = {**poses[i], "pos": np.round(chain[k][1], 3).tolist(), "quat": np.round(Rot.from_matrix(chain[k][0]).as_quat(), 6).tolist(), "src": "gyro"}
        json.dump({**{k: v for k, v in P.items() if k != "poses"}, "poses": span_out}, open(out_json, "w"))
        for k, i in enumerate(range(a, b + 1)):
            f = chain[k][0] @ np.array([0, 0, 1.0]); print(f"   #{i}: yaw {math.degrees(math.atan2(f[0], -f[2])):7.1f}  pos {np.round(chain[k][1], 1).tolist()}" + (f"  anchor yaw {math.degrees(math.atan2((R_of(poses[i]) @ np.array([0,0,1.0]))[0], -(R_of(poses[i]) @ np.array([0,0,1.0]))[2])):7.1f}" if poses[i]["src"] == "kept" else ""), flush=True)
    err = Rot.from_matrix(chain[-1][0].T @ Rb); clos_r = math.degrees(err.magnitude()); clos_t = Cb - chain[-1][1]; clos_m = float(np.linalg.norm(clos_t))
    if clos_r > CLOSE_MAX or clos_m > 3.0: report.append((a, b, clos_r, clos_m, min(inls))); continue
    for k, i in enumerate(range(a, b + 1)):
        t = k / (b - a); Rc = chain[k][0] @ Rot.from_rotvec(err.as_rotvec() * t).as_matrix(); Cc = chain[k][1] + clos_t * t
        if a < i < b:
            out[i]["quat"] = np.round(Rot.from_matrix(Rc).as_quat(), 6).tolist(); out[i]["gyro"] = True
            if POS: out[i]["pos"] = np.round(Cc, 3).tolist()
    report.append((a, b, clos_r, clos_m, min(inls)))
    if gi % 50 == 0: print(f"  {gi}/{len(gaps)} gaps, {(time.time()-t0)/max(nf,1):.2f} s/frame", flush=True)
if not SPAN: json.dump({**{k: v for k, v in P.items() if k != "poses"}, "poses": out}, open(out_json, "w"))
done = [r for r in report if r[2] is not None and r[2] <= CLOSE_MAX and r[3] <= 3.0]; skipped = [r for r in report if r not in done]
cr = np.array([r[2] for r in done]); cm = np.array([r[3] for r in done]); ln = np.array([r[1] - r[0] for r in done])
print(f"applied to {len(done)} gaps ({sum(r[1]-r[0]-1 for r in done)} frames): closure rot p50 {np.median(cr):.1f} p90 {np.percentile(cr,90):.1f} max {cr.max():.1f} deg ({np.median(cr/ln):.2f}/frame); pos p50 {np.median(cm):.2f} p90 {np.percentile(cm,90):.2f} m")
print(f"kept init in {len(skipped)} gaps ({sum(r[1]-r[0]-1 for r in skipped)} frames)")
for a, b, cr_, cm_, n in report:
    if b - a >= 8 or (cr_ is not None and cr_ > CLOSE_MAX): print(f"  #{a}-#{b} ({b-a-1}): " + ("track lost" if cr_ is None else f"closure {cr_:.1f} deg {cm_:.2f} m") + f", min inl {n}")
print("GYRO-DONE", flush=True)
