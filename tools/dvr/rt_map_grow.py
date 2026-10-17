"""Grow a keyframe map with real frames: the frames rt_track.py wrote with KFOUT (its own keypoints and descriptors on the
day's pictures, and the pose it solved), lifted to 3D with the scan's depth rendered at that pose, appended to a map from
rt_map.py as keyframes marked real. The geometry stays the scan's; the look is the day's light. rt_track.py RKF=1 matches them.
  rt_map_grow.py base.npz scene.ply out.npz kfdir [kfdir ...]
env: NEAR (1.0 m, as rt_map.py), STEP (1.0 m) / ROT (20 deg): a frame is dropped when a newer real one is this close in both,
     SINCE (s, 0 = all): only the frames written in the last SINCE seconds - the heats just before match the light, older ones hurt"""
import glob, os, sys, time, numpy as np, torch
from plyfile import PlyData
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from frustum import rasterize_culled
from scipy.spatial.transform import Rotation as Rot
dev = "cuda"; E = lambda k, d: float(os.environ.get(k, d)); NEAR, STEP, ROT = E("NEAR", 1.0), E("STEP", 1.0), E("ROT", 20)
base, ply, out = sys.argv[1:4]; SINCE = E("SINCE", 0)
files = sorted((f for d in sys.argv[4:] for f in glob.glob(f"{d}/*.npz")), key=os.path.getmtime, reverse=True)   # newest first: they win the thinning
if SINCE: files = [f for f in files if time.time() - os.path.getmtime(f) < SINCE]
m = dict(np.load(base)); TOPK = m["kp"].shape[1]; RW, RH = [int(v) for v in m["size"]]; K = m["K"]
if "real" not in m: m["real"] = np.zeros(len(m["pos"]), np.uint8)
v = PlyData.read(ply)["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.1]
T = lambda a: torch.tensor(a, dtype=torch.float32, device=dev)
means, quats = T(np.c_[v["x"], v["y"], v["z"]]), T(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]])
scales, opac = torch.exp(T(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]])), torch.sigmoid(T(v["opacity"]))
colors = T(np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :])   # depth only: the colour does not matter
def w2c(q, c): M = np.eye(4); R = Rot.from_quat(q).as_matrix(); M[:3, :3] = R.T; M[:3, 3] = -R.T @ c; return M
kept, P, F, t0 = [], [], [], time.time()
for f in files:                                      # thin as rt_map.py does, over the frames of every cell and run, the newest kept
    with np.load(f) as z: k = {n: z[n] for n in z.files}   # read and closed: a day has thousands of these
    fw = Rot.from_quat(k["quat"]).as_matrix()[:, 2]
    if not np.allclose(k["K"], K): sys.exit(f"{f}: its camera is not the map's")
    if P and ((np.linalg.norm(np.array(P) - k["pos"], axis=1) < STEP) & (np.array(F) @ fw > np.cos(np.radians(ROT)))).any(): continue
    P.append(k["pos"]); F.append(fw); kept.append(k)
print(f"{len(files)} frames -> {len(kept)} real keyframes", flush=True)
KP, DS, X3, NK, GD, Q, PS = [], [], [], [], [], [], []
for k in kept:
    with torch.no_grad():
        o, a, _ = rasterize_culled(means, quats, scales, opac, colors, T(w2c(k["quat"], k["pos"])[None]), T(K)[None], RW, RH, sh_degree=0, render_mode="RGB+ED", near_plane=NEAR)
    dep, al = o[0, ..., 3].cpu().numpy(), a[0, ..., 0].cpu().numpy()
    uv = k["kp"]; ix = np.clip(np.round(uv).astype(int), 0, [RW - 1, RH - 1]); d = dep[ix[:, 1], ix[:, 0]]
    ok = (al[ix[:, 1], ix[:, 0]] > 0.9) & (d > 0.3) & (d < 200); uv, d, desc = uv[ok][:TOPK], d[ok][:TOPK], k["desc"][ok][:TOPK]
    xc = np.c_[(uv[:, 0] - K[0, 2]) / K[0, 0], (uv[:, 1] - K[1, 2]) / K[1, 1], np.ones(len(uv))] * d[:, None]
    X = (np.linalg.inv(w2c(k["quat"], k["pos"])) @ np.c_[xc, np.ones(len(xc))].T).T[:, :3]
    n = len(uv); pad = TOPK - n; NK.append(n); Q.append(k["quat"]); PS.append(k["pos"]); GD.append(k["g"])
    KP.append(np.pad(uv, ((0, pad), (0, 0)))); DS.append(np.pad(desc, ((0, pad), (0, 0)))); X3.append(np.pad(X, ((0, pad), (0, 0))))
if not kept: sys.exit("no frame to add")
cat = lambda a, b, t: np.concatenate([a, np.array(b, t)])
np.savez(out, pos=cat(m["pos"], PS, np.float32), quat=cat(m["quat"], Q, np.float32), K=K, size=m["size"], n=cat(m["n"], NK, m["n"].dtype),
         kp=cat(m["kp"], KP, np.float32), desc=cat(m["desc"], DS, np.float16), X=cat(m["X"], X3, np.float32), g=cat(m["g"], GD, np.float32),
         real=np.concatenate([m["real"], np.ones(len(kept), np.uint8)]))
print(f"GROW-DONE {len(m['pos'])} + {len(kept)} real keyframes, median {int(np.median(NK))} points with depth, {time.time() - t0:.0f} s", flush=True)
