"""Place every DVR frame on the 3DGS scan from nothing but the video: relocalize against renders at the scan's own
camera poses (DINOv2 retrieval, then MASt3R match + PnP), then track frame by frame (render at the constant-velocity
prediction, match, PnP). Writes cpr_match.py's records and correspondence dumps, and an init track for cpr_ba.py.
usage (mastenv, with gsplat): cpr_track.py scene.ply dvr_pinhole.mp4 scan_cameras.json out_dir
env: SUB (4), RELOC_TOPK (5), MIN_INL (100), LOST (10 frames before relocalizing), NMAX / FIRST (only frames FIRST..NMAX-1, for tests), START (relocalize here first)"""
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
SUB, TOPK, MIN_INL, LOST, NMAX = int(E("SUB", 4)), int(E("RELOC_TOPK", 5)), int(E("MIN_INL", 100)), int(E("LOST", 10)), int(E("NMAX", 0))
START, FIRST, DEBUG = int(E("START", -1)), int(E("FIRST", 0)), os.environ.get("DEBUG") == "1"
PROBE = E("PROBE", 2.0)                            # m: second render this far off, to see if the answer follows the viewpoint
ply, video, scan_json, out_dir = sys.argv[1:5]; os.makedirs(f"{out_dir}/dump", exist_ok=True)
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
        k = qvalid[a[:, 1], a[:, 0]] & (al[c][b[:, 1], b[:, 0]] > 0.9) & (d > 0.3) & (d < 200)
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
# ---- relocalization: DINOv2 retrieval over renders at the scan cameras
dino = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14").to(dev).eval()
MEAN, STD = T([0.485, 0.456, 0.406])[:, None, None], T([0.229, 0.224, 0.225])[:, None, None]
def gdesc(imgs):
    x = torch.stack([(T(cv2.resize(im, (224, 168))).permute(2, 0, 1) / 255 - MEAN) / STD for im in imgs])
    with torch.no_grad(): f = dino(x)
    return torch.nn.functional.normalize(f, dim=1)
scan = json.load(open(scan_json))["cameras"]; refs = [(Rot.from_quat(c["quat"]), np.array(c["pos"])) for c in scan]
ref_desc = torch.cat([gdesc(render(refs[k:k + 16])[0]) for k in range(0, len(refs), 16)])
print(f"{len(v):,} splats, {len(refs)} scan cameras as references", flush=True)
def relocalize(q):
    top = torch.topk(ref_desc @ gdesc([q])[0], TOPK).indices.tolist()
    rgb, dep, al, vm = render([refs[k] for k in top]); r = solve(q, rgb, dep, al, vm)
    if DEBUG: print(f"reloc top {top} -> {r[0]['inliers'] if r else None}", flush=True)
    if r is None or r[0]["inliers"] < 30: return None   # a reference view metres away matches weakly; judge after re-rendering
    for _ in range(3):                              # match again from the pose it found
        rgb, dep, al, vm = render([(Rot.from_quat(r[0]["quat"]), np.array(r[0]["pos"]))]); r2 = solve(q, rgb, dep, al, vm)
        if r2 is None or r2[0]["inliers"] <= r[0]["inliers"]: break
        r = r2
    if DEBUG: print(f"reloc refined -> {r[0]['inliers']}", flush=True)
    return r if r[0]["inliers"] >= MIN_INL else None
# ---- frames
cap = cv2.VideoCapture(video); frames = []
while True:
    ok, fr = cap.read()
    if not ok or (NMAX and len(frames) >= NMAX): break
    frames.append(cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), (RW, RH), interpolation=cv2.INTER_AREA))
N = len(frames); rec = {}; t0 = time.time()
def keep(i, r):
    rec[i] = dict(i=i, **r[0]); np.savez(f"{out_dir}/dump/{i:05d}.npz", X=r[1].astype(np.float32), uv=r[2].astype(np.float32), inl=r[3])
def plausible(r, pred, dtf):                         # rejects an answer from another place, not PnP noise (the BA takes that)
    return r is not None and r[0]["inliers"] >= MIN_INL and np.linalg.norm(np.array(r[0]["pos"]) - pred[1]) < 3.0 + 1.0 * dtf and \
        np.degrees((Rot.from_quat(r[0]["quat"]).inv() * pred[0]).magnitude()) < 20 + 10 * dtf
def track(order, seed):
    hist, lost = [seed], 0                           # hist: (frame, Rot, pos) of the last tracked frames in this direction
    for i in order:
        if len(hist) >= 2:                           # velocity fitted over the last few: a single step carries the PnP noise
            fi = np.array([h[0] for h in hist], float); Pp = np.array([h[2] for h in hist])
            vel = np.polyfit(fi, Pp, 1)[0]; ib, Rb, pb = hist[-1]
            w = np.mean([(a[1].inv() * b[1]).as_rotvec() / (b[0] - a[0]) for a, b in zip(hist[:-1], hist[1:])], axis=0)
            pred = (Rb * Rot.from_rotvec(w * (i - ib)), pb + vel * (i - ib))
        elif hist: pred = (hist[-1][1], hist[-1][2])
        else: pred = None
        r = None
        if pred is not None and lost < LOST:
            dtf = abs(i - hist[-1][0])
            rgb, dep, al, vm = render([pred]); r = solve(frames[i], rgb, dep, al, vm)
            if DEBUG and r is not None: print(f"#{i} dtf {dtf} inl {r[0]['inliers']} dpos {np.linalg.norm(np.array(r[0]['pos']) - pred[1]):.2f} drot {np.degrees((Rot.from_quat(r[0]['quat']).inv() * pred[0]).magnitude()):.1f}", flush=True)
            if not plausible(r, pred, dtf):
                views = [(pred[0] * Rot.from_rotvec([0, math.radians(y), 0]), pred[1]) for y in (0, -25, 25)]
                rgb, dep, al, vm = render(views); r = solve(frames[i], rgb, dep, al, vm)
                if not plausible(r, pred, dtf): r = None
        else:
            r = relocalize(frames[i])
            if r is not None: hist = []              # the frames before the loss say nothing about the velocity here
        if r is None: lost += 1; continue
        if PROBE and pred is not None:               # observability: does the position answer move with where we render from?
            ans = np.array(r[0]["pos"]); side = Rot.from_quat(r[0]["quat"]).as_matrix() @ np.array([1.0, 0, 1.0]) / math.sqrt(2)
            rgb, dep, al, vm = render([(Rot.from_quat(r[0]["quat"]), ans + PROBE * side)]); r2 = solve(frames[i], rgb, dep, al, vm)
            r[0]["k"] = round(float(np.linalg.norm(np.array(r2[0]["pos"]) - ans) / PROBE), 3) if r2 and r2[0]["inliers"] >= MIN_INL else None
        lost = 0; keep(i, r); hist = (hist + [(i, Rot.from_quat(r[0]["quat"]), np.array(r[0]["pos"]))])[-6:]
        if len(rec) % 200 == 0: print(f"{len(rec)} tracked, at #{i}, {(time.time() - t0) / len(rec):.2f} s/frame", flush=True)
start = next((i for i in (range(0, N, 15) if START < 0 else [START]) if (r := relocalize(frames[i])) is not None and (keep(i, r) or True)), None)
if start is None: sys.exit("relocalization never succeeded")
print(f"relocalized at #{start} ({rec[start]['inliers']} inliers)", flush=True)
seed = (start, Rot.from_quat(rec[start]["quat"]), np.array(rec[start]["pos"]))
track(range(start + 1, N), seed); track(range(start - 1, FIRST - 1, -1), seed)
with open(f"{out_dir}/track.jsonl", "w") as f:
    for i in sorted(rec): f.write(json.dumps(rec[i]) + "\n")
# init track for cpr_ba.py: tracked frames as they are, the rest interpolated between the nearest tracked ones
ks = sorted(rec); Rk = Rot.from_quat([rec[i]["quat"] for i in ks]); Pk = np.array([rec[i]["pos"] for i in ks]); sl = Slerp(ks, Rk)
poses = []
for i in range(N):
    j = min(max(i, ks[0]), ks[-1])
    poses.append({"i": i, "t": round(cam["t0"] + i / cam.get("fps", 60), 4), "pos": np.round([np.interp(j, ks, Pk[:, a]) for a in range(3)], 3).tolist(),
                  "quat": np.round(sl([j]).as_quat()[0], 6).tolist(), "src": "track" if i in rec else "interp"})
json.dump({"frame": "web: x east, y up, z south, metres; quat (x,y,z,w) world-from-camera, COLMAP camera axes", "t0": cam["t0"], "fps": cam.get("fps", 60), "poses": poses},
          open(f"{out_dir}/track_init.json", "w"))
print(f"TRACK-DONE {len(rec)} of {N} frames, {time.time() - t0:.0f} s", flush=True)
