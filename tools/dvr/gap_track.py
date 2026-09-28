"""Track again, from both ends, through every stretch cpr_ba.py could only bridge with its motion prior ("ba-fill").
The first pass predicts from the last frames it tracked and gives up after LOST misses; at a fast pilot's rates (KNT:
90th percentile 38 m/s and 685 deg/s at 30 fps, 23 deg per frame) the render drifts off the picture and a gap of up to
two seconds (60 m) is left to the prior, which draws a different line on every lap. Here each gap is entered from the
solved frame beside it, forward from its start and backward from its end, predicting from the solved poses and trying
ten renders per frame: the prediction, the prior's own fill, yaw +-15/+-30, pitch +-15 and roll +-20. Two misses in a
row end that direction. Records and dumps as cpr_track.py (k measured the same way), so cpr_ba.py
takes out_dir as a later pair.
usage (mastenv, with gsplat): gap_track.py scene.ply dvr_pinhole.mp4 poses.json out_dir   env: MIN_GAP (3), MIN_INL (100), CLEAN (dir from hdz_blocks.py), TRACK (round 1's track.jsonl)"""
import json, sys, os, time, math, numpy as np, torch, cv2
sys.path.insert(0, os.path.expanduser("~/mast3r"))
from mast3r.model import AsymmetricMASt3R
from mast3r.fast_nn import fast_reciprocal_NNs
from dust3r.inference import inference
from plyfile import PlyData
from gsplat import rasterization
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from frustum import rasterize_culled   # centre-in-view culling, as PlayCanvas
from scipy.spatial.transform import Rotation as Rot, Slerp
dev = "cuda"; E = lambda k, d: float(os.environ.get(k, d))
# NEAR (m): splats whose centre is closer than this are not drawn. Flying 0.5 m over the grass, the scan's large thin
# ground splats right in front of the camera spread over the whole render as fog (KNT #380-#388: at 0.01 m the ground,
# pylon and trees vanish; at 1 m they are back). The cost: a real object within NEAR of the lens disappears too.
NEAR = float(os.environ.get("NEAR", 1.0))
SUB, MIN_INL, MIN_GAP, PROBE = 4, int(os.environ.get("MIN_INL", 100)), int(os.environ.get("MIN_GAP", 3)), 2.0
ply, video, poses_path, out_dir = sys.argv[1:5]; os.makedirs(f"{out_dir}/dump", exist_ok=True)
# ---- scene (as cpr_render.py)
v = PlyData.read(ply)["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.1]
T = lambda a: torch.tensor(a, dtype=torch.float32, device=dev)
means, quats = T(np.c_[v["x"], v["y"], v["z"]]), T(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]])
scales, opac = torch.exp(T(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]])), torch.sigmoid(T(v["opacity"]))
sh0 = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :]; rest = np.stack([np.c_[[v[f"f_rest_{c*15+k}"] for k in range(15)]].T for c in range(3)], axis=2)
colors = T(np.concatenate([sh0, rest], axis=1)[:, :4])
cam = json.load(open(video + ".json")); W, H = cam["width"], cam["height"]; RW = 512; s = RW / W; RH = int(round(H * s))
Kfull = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]]); Kr = Kfull * [[s], [s], [1]]; Krt = T(Kr)
fk = cam["source_fisheye"]; Kf = np.array([[fk["fx"], 0, fk["cx"]], [0, fk["fy"], fk["cy"]], [0, 0, 1]])
m1, m2 = cv2.fisheye.initUndistortRectifyMap(Kf, np.array(fk["k"]), np.eye(3), Kfull, (W, H), cv2.CV_16SC2)
osd = np.full((H, W), 255, np.uint8); osd[:82] = 0; osd[632:] = 0
qvalid = cv2.resize((cv2.erode(cv2.remap(osd, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0), np.ones((9, 9), np.uint8)) > 0).astype(np.uint8), (RW, RH), interpolation=cv2.INTER_NEAREST) > 0
def w2c(R, c): M = np.eye(4); M[:3, :3] = R.as_matrix().T; M[:3, 3] = -M[:3, :3] @ c; return M
def render(views):                                   # [(Rot, pos)] -> rgb uint8 (B,h,w,3), depth, alpha, w2c
    vm = np.stack([w2c(R, c) for R, c in views])
    with torch.no_grad():
        o, a, _ = rasterize_culled(means, quats, scales, opac, colors, T(vm), Krt[None].expand(len(vm), 3, 3), RW, RH, sh_degree=1, render_mode="RGB+ED", near_plane=NEAR)
    return (o[..., :3].clamp(0, 1) * 255).byte().cpu().numpy(), o[..., 3].cpu().numpy(), a[..., 0].cpu().numpy(), vm
# ---- matching (as cpr_match.py)
model = AsymmetricMASt3R.from_pretrained("naver/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric").to(dev).eval()
def view(img, idx): return dict(img=torch.tensor(img, dtype=torch.float32).permute(2, 0, 1)[None] / 255 * 2 - 1, true_shape=np.int32([img.shape[:2]]), idx=idx, instance=str(idx))
def solve(q, rgb, dep, al, vm):                      # best PnP over the candidate renders -> record, X, uv, RANSAC inlier mask
    with torch.no_grad(): o = inference([(view(q, 0), view(rgb[c], 1)) for c in range(len(rgb))], model, dev, batch_size=len(rgb), verbose=False)
    best = None
    for c in range(len(rgb)):
        a, b = fast_reciprocal_NNs(o["pred1"]["desc"][c], o["pred2"]["desc"][c], subsample_or_initxy1=SUB, device=dev, dist="dot", block_size=2**13)
        a, b = np.asarray(a), np.asarray(b); d = dep[c][b[:, 1], b[:, 0]]
        ok_px = qvalid if NOISE is None else qvalid & ~NOISE        # drop matches on painted-over dropout blocks
        k = ok_px[a[:, 1], a[:, 0]] & (al[c][b[:, 1], b[:, 0]] > 0.9) & (d > 0.3) & (d < 200)
        a, b, d = a[k], b[k], d[k]
        if len(a) < 12: continue
        xc = np.c_[(b[:, 0] - Kr[0, 2]) / Kr[0, 0], (b[:, 1] - Kr[1, 2]) / Kr[1, 1], np.ones(len(b))] * d[:, None]
        X = (np.linalg.inv(vm[c]) @ np.c_[xc, np.ones(len(xc))].T).T[:, :3]; uv = (a + 0.5) / s - 0.5
        ok, rv, tv, inl = cv2.solvePnPRansac(X, uv, Kfull, None, iterationsCount=2000, reprojectionError=4.0, flags=cv2.SOLVEPNP_SQPNP)
        if not ok or inl is None: continue
        inl = inl[:, 0]; rv, tv = cv2.solvePnPRefineLM(X[inl], uv[inl], Kfull, None, rv, tv)
        if best is None or len(inl) > best[0]["inliers"]:
            Rw = cv2.Rodrigues(rv)[0]; C = -Rw.T @ tv[:, 0]
            m = np.zeros(len(X), bool); m[inl] = True
            # every match goes to the dump with its RANSAC verdict: with thousands of near-ground points the consensus
            # pose fits them and throws out the few far ones (poles, tree lines) that disagree with it by 15-20 px -
            # the very points that would have corrected it. cpr_ba.py decides what to do with them.
            best = (dict(quat=Rot.from_matrix(Rw.T).as_quat().tolist(), pos=C.tolist(), inliers=int(len(inl)),
                         near=round(float(np.mean(np.linalg.norm(X[inl] - C, axis=1) < 15)), 3)), X, uv, m)
    return best
# ---- gaps
J = json.load(open(poses_path)); P = J["poses"]; src = [(p or {}).get("src") for p in P]
solved = lambda i: 0 <= i < len(P) and src[i] in ("ba", "takeoff")
gaps, s0 = [], None
for i in range(len(P)):
    if src[i] == "ba-fill":
        s0 = i if s0 is None else s0
    elif s0 is not None:
        if i - s0 >= MIN_GAP and solved(s0 - 1) and solved(i): gaps.append((s0, i - 1))
        s0 = None
need = sorted({i for a, b in gaps for i in range(a, b + 1)})
print(f"{len(gaps)} gaps of {MIN_GAP}+ frames, {len(need)} frames", flush=True)
# CLEAN=<dir>: the frames with dropout blocks painted over and their masks (hdz_blocks.py), in place of the raw video
CLEAN = os.environ.get("CLEAN"); NOISE = None
masks = np.load(f"{CLEAN}/noise_masks.npz")["masks"] if CLEAN else None
cap = cv2.VideoCapture(f"{CLEAN}/dvr_pinhole_clean.mp4" if CLEAN else video); frames = {}; i = 0
while True:
    ok, fr = cap.read()
    if not ok: break
    if i in need: frames[i] = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), (RW, RH), interpolation=cv2.INTER_AREA)
    i += 1
pose = lambda i: (Rot.from_quat(P[i]["quat"]), np.array(P[i]["pos"]))
# TRACK=<track.jsonl>: round 1's own answers. In a tight turn the prediction runs off and the bundle adjustment, bridging
# forty frames on its motion prior, rejects round 1's answer because it disagrees with that bridge - yet it was nearly
# right (KNT #380-#390: flag, pylon and tree line in place, the bridge looking into the grass). Rendering from it is
# then the one view that can match, and a result near it is accepted even when far from the prediction.
T1 = {}
if os.environ.get("TRACK"):
    for l in open(os.environ["TRACK"]):
        r_ = json.loads(l); T1[r_["i"]] = (Rot.from_quat(r_["quat"]), np.array(r_["pos"]))
def candidates(pred, fill, t1=None):
    Rb, pb = pred; turn = lambda ax, d: Rb * Rot.from_rotvec(np.radians(d) * np.eye(3)[ax])
    return [pred, fill] + ([t1] if t1 is not None else []) + [(turn(1, y), pb) for y in (-15, 15, -30, 30)] + \
        [(turn(0, p), pb) for p in (-15, 15)] + [(turn(2, r), pb) for r in (-20, 20)]
def plausible(r, pred, dtf):
    return r is not None and r[0]["inliers"] >= MIN_INL and np.linalg.norm(np.array(r[0]["pos"]) - pred[1]) < 3.0 + 1.0 * dtf and \
        np.degrees((Rot.from_quat(r[0]["quat"]).inv() * pred[0]).magnitude()) < 20 + 10 * dtf
rec, t0 = {}, time.time()
def walk(order, edge, step, seed=None):
    hist = [seed] if seed else [(j, *pose(j)) for j in range(edge - 5 * step, edge + step, step) if solved(j)][-6:]
    miss = 0
    for i in order:
        if i in rec: break                                   # met the other direction
        fi = np.array([h[0] for h in hist], float); Pp = np.array([h[2] for h in hist]); ib, Rb, pb = hist[-1]
        vel = np.polyfit(fi, Pp, 1)[0] if len(hist) > 1 else np.zeros(3)
        w = np.mean([(a[1].inv() * b[1]).as_rotvec() / (b[0] - a[0]) for a, b in zip(hist[:-1], hist[1:])], axis=0) if len(hist) > 1 else np.zeros(3)
        pred = (Rb * Rot.from_rotvec(w * (i - ib)), pb + vel * (i - ib)); dtf = abs(i - ib)
        global NOISE
        NOISE = cv2.resize(masks[i].astype(np.uint8), (RW, RH), interpolation=cv2.INTER_NEAREST) > 0 if masks is not None and i < len(masks) else None
        rgb, dep, al, vm = render(candidates(pred, pose(i), T1.get(i))); r = solve(frames[i], rgb, dep, al, vm)
        if not (plausible(r, pred, dtf) or (i in T1 and plausible(r, T1[i], 0))):
            miss += 1
            if miss >= 2: break
            continue
        miss = 0; ans = np.array(r[0]["pos"]); side = Rot.from_quat(r[0]["quat"]).as_matrix() @ np.array([1.0, 0, 1.0]) / math.sqrt(2)
        rgb, dep, al, vm = render([(Rot.from_quat(r[0]["quat"]), ans + PROBE * side)]); r2 = solve(frames[i], rgb, dep, al, vm)
        r[0]["k"] = round(float(np.linalg.norm(np.array(r2[0]["pos"]) - ans) / PROBE), 3) if r2 and r2[0]["inliers"] >= MIN_INL else None
        rec[i] = dict(i=i, **r[0]); np.savez(f"{out_dir}/dump/{i:05d}.npz", X=r[1].astype(np.float32), uv=r[2].astype(np.float32), inl=r[3])
        hist = (hist + [(i, Rot.from_quat(r[0]["quat"]), ans)])[-6:]
def keep(i, r, ref):
    ans = np.array(r[0]["pos"]); side = Rot.from_quat(r[0]["quat"]).as_matrix() @ np.array([1.0, 0, 1.0]) / math.sqrt(2)
    rgb, dep, al, vm = render([(Rot.from_quat(r[0]["quat"]), ans + PROBE * side)]); r2 = solve(frames[i], rgb, dep, al, vm)
    r[0]["k"] = round(float(np.linalg.norm(np.array(r2[0]["pos"]) - ans) / PROBE), 3) if r2 and r2[0]["inliers"] >= MIN_INL else None
    rec[i] = dict(i=i, **r[0]); np.savez(f"{out_dir}/dump/{i:05d}.npz", X=r[1].astype(np.float32), uv=r[2].astype(np.float32), inl=r[3])
def islands(a, b):
    """Frames the two walks never reached (each stops at its second miss) but round 1 answered: match from round 1's
    answer alone, and walk out of every one that holds. KNT #380-#390 sat 15 frames past where both walks stopped."""
    for i in range(a, b + 1):
        if i in rec or i not in T1: continue
        global NOISE
        NOISE = cv2.resize(masks[i].astype(np.uint8), (RW, RH), interpolation=cv2.INTER_NEAREST) > 0 if masks is not None and i < len(masks) else None
        Rt, pt = T1[i]; views = [T1[i]] + [(Rt * Rot.from_rotvec(np.radians(d) * np.eye(3)[1]), pt) for d in (-15, 15)]
        rgb, dep, al, vm = render(views); r = solve(frames[i], rgb, dep, al, vm)
        if not plausible(r, T1[i], 0): continue
        keep(i, r, T1[i]); seed = (i, Rot.from_quat(r[0]["quat"]), np.array(r[0]["pos"]))
        walk(range(i + 1, b + 1), None, 1, seed); walk(range(i - 1, a - 1, -1), None, -1, seed)
for g, (a, b) in enumerate(gaps):
    walk(range(a, b + 1), a - 1, 1); walk(range(b, a - 1, -1), b + 1, -1)
    if T1: islands(a, b)
    if g % 10 == 9: print(f"{g + 1}/{len(gaps)} gaps, {len(rec)} of {len(need)} frames recovered, {time.time() - t0:.0f} s", flush=True)
with open(f"{out_dir}/gap.jsonl", "w") as f:
    for i in sorted(rec): f.write(json.dumps(rec[i]) + "\n")
print(f"recovered {len(rec)} of {len(need)} gap frames in {time.time() - t0:.0f} s")
