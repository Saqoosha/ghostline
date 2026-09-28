"""Round 2 of the matching: render the 3DGS at each frame's solved pose (poses.json from cpr_ba.py + takeoff.py), match
the DVR frame to it with MASt3R, solve PnP, and dump every match with its RANSAC verdict - the same records and dumps
as cpr_track.py, so cpr_ba.py takes them as a later pair that replaces round 1 frame by frame.
Why: round 1 rendered at a constant-velocity prediction; over near grass its consensus pose fits the ground and the far
objects it gets wrong are thrown out as outliers. Rendered at the solved pose the far field lines up closely enough to
be matched, and cpr_ba.py's distance quotas give it a say in the rotation.
k (how much the position answer echoes the render viewpoint) is copied from round 1: it is a property of what the frame
sees, and re-measuring it doubles the render cost.
usage (mastenv, with gsplat): cpr_rematch.py scene.ply dvr_pinhole.mp4 poses.json track.jsonl out_dir   env: FIRST, LAST
writes out_dir/rematch.jsonl and out_dir/dump/NNNNN.npz"""
import json, sys, os, numpy as np, torch, cv2
sys.path.insert(0, os.path.expanduser("~/mast3r"))
from mast3r.model import AsymmetricMASt3R
from mast3r.fast_nn import fast_reciprocal_NNs
from dust3r.inference import inference
from plyfile import PlyData
from gsplat import rasterization
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from frustum import rasterize_culled   # centre-in-view culling, as PlayCanvas
from scipy.spatial.transform import Rotation as Rot
dev = "cuda"; SUB = 4
# NEAR (m): splats whose centre is closer than this are not drawn. Flying 0.5 m over the grass, the scan's large thin
# ground splats right in front of the camera spread over the whole render as fog (KNT #380-#388: at 0.01 m the ground,
# pylon and trees vanish; at 1 m they are back). The cost: a real object within NEAR of the lens disappears too.
NEAR = float(os.environ.get("NEAR", 1.0))
ply, video, poses_path, track_jl, out_dir = sys.argv[1:6]; os.makedirs(f"{out_dir}/dump", exist_ok=True)
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
model = AsymmetricMASt3R.from_pretrained("naver/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric").to(dev).eval()
def view(img, idx): return dict(img=torch.tensor(img, dtype=torch.float32).permute(2, 0, 1)[None] / 255 * 2 - 1, true_shape=np.int32([img.shape[:2]]), idx=idx, instance=str(idx))
# frames the bundle adjustment solved or bridged; "interp" is the tracker's hold after it lost the drone for good
# (a crash: the feed turns to noise and grey), "ground" is the pad - neither has a pose worth checking or matching
SOLVED = ("ba", "ba-fill", "takeoff")
P = json.load(open(poses_path))["poses"]; K1 = {}
for l in open(track_jl):
    r = json.loads(l); K1[r["i"]] = r.get("k")
FIRST, LAST = int(os.environ.get("FIRST", 0)), int(os.environ.get("LAST", len(P) - 1))
cap = cv2.VideoCapture(video); cap.set(cv2.CAP_PROP_POS_FRAMES, FIRST); n = 0
with open(f"{out_dir}/rematch.jsonl", "w") as fo:
    for i in range(FIRST, LAST + 1):
        ok, fr = cap.read()
        if not ok: break
        if not P[i] or P[i]["src"] not in SOLVED: continue
        q = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), (RW, RH), interpolation=cv2.INTER_AREA)
        rgb, dep, al, vm = render([(Rot.from_quat(P[i]["quat"]), np.array(P[i]["pos"]))])
        with torch.no_grad(): o = inference([(view(q, 0), view(rgb[0], 1))], model, dev, batch_size=1, verbose=False)
        a, b = fast_reciprocal_NNs(o["pred1"]["desc"][0], o["pred2"]["desc"][0], subsample_or_initxy1=SUB, device=dev, dist="dot", block_size=2**13)
        a, b = np.asarray(a), np.asarray(b); d = dep[0][b[:, 1], b[:, 0]]
        k = qvalid[a[:, 1], a[:, 0]] & (al[0][b[:, 1], b[:, 0]] > 0.9) & (d > 0.3) & (d < 200)
        a, b, d = a[k], b[k], d[k]
        if len(a) < 12: continue
        xc = np.c_[(b[:, 0] - Kr[0, 2]) / Kr[0, 0], (b[:, 1] - Kr[1, 2]) / Kr[1, 1], np.ones(len(b))] * d[:, None]
        X = (np.linalg.inv(vm[0]) @ np.c_[xc, np.ones(len(xc))].T).T[:, :3]; uv = (a + 0.5) / s - 0.5
        okp, rv, tv, inl = cv2.solvePnPRansac(X, uv, Kfull, None, iterationsCount=2000, reprojectionError=4.0, flags=cv2.SOLVEPNP_SQPNP)
        if not okp or inl is None: continue
        inl = inl[:, 0]; rv, tv = cv2.solvePnPRefineLM(X[inl], uv[inl], Kfull, None, rv, tv)
        Rw = cv2.Rodrigues(rv)[0]; C = -Rw.T @ tv[:, 0]; m = np.zeros(len(X), bool); m[inl] = True
        rec = dict(i=i, quat=Rot.from_matrix(Rw.T).as_quat().tolist(), pos=C.tolist(), inliers=int(len(inl)),
                   near=round(float(np.mean(np.linalg.norm(X[inl] - C, axis=1) < 15)), 3), k=K1.get(i))
        fo.write(json.dumps(rec) + "\n"); n += 1
        np.savez(f"{out_dir}/dump/{i:05d}.npz", X=X.astype(np.float32), uv=uv.astype(np.float32), inl=m)
        if n % 500 == 0: print(f"{n} rematched, at #{i}", flush=True)
print(f"{n} frames rematched")
