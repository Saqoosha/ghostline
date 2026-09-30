"""Keyframe map for the live tracker (rt_track.py): render the 3DGS at poses along earlier flights of the same course,
extract XFeat keypoints on each render and lift them to 3D with the rendered depth. Built once per scene, offline.
The flight to be tracked must not be among the inputs - the point is to see how a new flight fares on a map made
from the others (race pilots fly nearly the same line, so the views the map needs are the ones earlier flights took).
usage (mastenv, with gsplat + kornia, ~/xfeat cloned): rt_map.py scene.ply cam.json out.npz poses.json [poses.json ...] [scan_cameras.json]
env: STEP (1.0 m) / ROT (20 deg): a pose becomes a keyframe when no kept one is this close in both; TOPK (2048) keypoints;
     RW (640) render width; NEAR (1.0 m, as cpr_track.py); CULL (1.2, frustum.py)"""
import json, sys, os, time, numpy as np, torch
sys.path.insert(0, os.path.expanduser("~/xfeat")); from modules.xfeat import XFeat
from plyfile import PlyData
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from frustum import rasterize_culled
from scipy.spatial.transform import Rotation as Rot
dev = "cuda"; E = lambda k, d: float(os.environ.get(k, d))
STEP, ROT, TOPK, RW, NEAR = E("STEP", 1.0), E("ROT", 20), int(E("TOPK", 2048)), int(E("RW", 640)), E("NEAR", 1.0)
ply, cam_json, out = sys.argv[1:4]; inputs = sys.argv[4:]
v = PlyData.read(ply)["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.1]
T = lambda a: torch.tensor(a, dtype=torch.float32, device=dev)
means, quats = T(np.c_[v["x"], v["y"], v["z"]]), T(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]])
scales, opac = torch.exp(T(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]])), torch.sigmoid(T(v["opacity"]))
sh0 = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :]; rest = np.stack([np.c_[[v[f"f_rest_{c*15+k}"] for k in range(15)]].T for c in range(3)], axis=2)
colors = T(np.concatenate([sh0, rest], axis=1)[:, :4])
cam = json.load(open(cam_json)); s = RW / cam["width"]; RH = int(round(cam["height"] * s))
Kr = np.array([[cam["fx"] * s, 0, cam["cx"] * s], [0, cam["fy"] * s, cam["cy"] * s], [0, 0, 1]])
# ---- candidate poses: flown frames of the other flights, then the scan cameras (they cover the pad and the ground level)
cand = []
for f in inputs:
    d = json.load(open(f))
    if "cameras" in d: cand += [(c["quat"], c["pos"]) for c in d["cameras"]]
    else: cand += [(p["quat"], p["pos"]) for p in d["poses"] if p.get("src") in ("ba", "ba-fill", "takeoff", "ground")]
CQ, CP = np.array([c[0] for c in cand]), np.array([c[1] for c in cand]); CF = Rot.from_quat(CQ).as_matrix()[:, :, 2]
P, F, n, keep = np.zeros((len(cand), 3)), np.zeros((len(cand), 3)), 0, []
for i in range(len(cand)):                          # greedy thinning: skip a pose a kept keyframe already covers
    if n and ((np.linalg.norm(P[:n] - CP[i], axis=1) < STEP) & (F[:n] @ CF[i] > np.cos(np.radians(ROT)))).any(): continue
    P[n], F[n] = CP[i], CF[i]; n += 1; keep.append(i)
keep_q, keep_p = CQ[keep].tolist(), CP[keep]
print(f"{len(cand)} candidate poses -> {len(keep_p)} keyframes", flush=True)
# ---- render, detect, lift
xfeat = XFeat(top_k=TOPK)
dino = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14").to(dev).eval()   # global descriptor, to relocalize when lost
MEAN, STD = T([0.485, 0.456, 0.406])[None, :, None, None], T([0.229, 0.224, 0.225])[None, :, None, None]
def gdesc(x):                                       # x (B, 3, H, W) in 0..1 -> (B, 384) unit vectors, as cpr_track.py
    with torch.no_grad(): return torch.nn.functional.normalize(dino((torch.nn.functional.interpolate(x, (168, 224), mode="area") - MEAN) / STD), dim=1)
def w2c(q, c): M = np.eye(4); R = Rot.from_quat(q).as_matrix(); M[:3, :3] = R.T; M[:3, 3] = -R.T @ c; return M
KP, DS, X3, NK, GD = [], [], [], [], []; t0 = time.time(); B = 8
for k in range(0, len(keep_p), B):
    vm = np.stack([w2c(q, c) for q, c in zip(keep_q[k:k + B], keep_p[k:k + B])])
    with torch.no_grad():
        o, a, _ = rasterize_culled(means, quats, scales, opac, colors, T(vm), T(Kr)[None].expand(len(vm), 3, 3), RW, RH, sh_degree=1, render_mode="RGB+ED", near_plane=NEAR)
        img = o[..., :3].clamp(0, 1).permute(0, 3, 1, 2); feats = xfeat.detectAndCompute(img, top_k=TOPK); GD.append(gdesc(img).cpu().numpy())
    dep, al = o[..., 3], a[..., 0]
    for j, f in enumerate(feats):
        uv = f["keypoints"]; ix = uv.round().long(); ix[:, 0].clamp_(0, RW - 1); ix[:, 1].clamp_(0, RH - 1)
        d = dep[j][ix[:, 1], ix[:, 0]]; ok = (al[j][ix[:, 1], ix[:, 0]] > 0.9) & (d > 0.3) & (d < 200)
        uv, d, desc = uv[ok].cpu().numpy(), d[ok].cpu().numpy(), f["descriptors"][ok].cpu().numpy()
        xc = np.c_[(uv[:, 0] - Kr[0, 2]) / Kr[0, 0], (uv[:, 1] - Kr[1, 2]) / Kr[1, 1], np.ones(len(uv))] * d[:, None]
        X = (np.linalg.inv(vm[j]) @ np.c_[xc, np.ones(len(xc))].T).T[:, :3]
        n = len(uv); NK.append(n); pad = TOPK - n
        KP.append(np.pad(uv, ((0, pad), (0, 0)))); DS.append(np.pad(desc, ((0, pad), (0, 0)))); X3.append(np.pad(X, ((0, pad), (0, 0))))
    if k % 800 == 0: print(f"{k + len(vm)} rendered, {(time.time() - t0) / (k + len(vm)) * 1000:.0f} ms each, {np.mean(NK):.0f} keypoints on the scene", flush=True)
np.savez(out, pos=np.array(keep_p, np.float32), quat=np.array(keep_q, np.float32), K=Kr, size=np.array([RW, RH]), n=np.array(NK),
         kp=np.array(KP, np.float32), desc=np.array(DS, np.float16), X=np.array(X3, np.float32), g=np.concatenate(GD).astype(np.float32))
print(f"MAP-DONE {len(keep_p)} keyframes, median {int(np.median(NK))} keypoints with depth, {time.time() - t0:.0f} s", flush=True)
