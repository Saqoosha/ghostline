"""How far the 3DGS render at each frame's solved pose sits from the DVR frame, in pixels, split by depth.
Render at the pose, match DVR and render with MASt3R, and take each match's pixel displacement: with the pose right it
is ~0, and a pose that fits the near ground but is off in rotation/position shows up as displacement on far objects
(poles, tree lines) - what a person sees. Near grass is repetitive and MASt3R tends to pair a pixel with the same pixel,
so the near bins read low; judge by the far bins.
usage (mastenv, with gsplat): eval_align.py scene.ply dvr_pinhole.mp4 poses.json out.json   env: STEP (10), FIRST (0), LAST
out.json: per frame {i, n, bins: {"0-5": [n, p50 px], ...}} and a summary printed at the end."""
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
# NEAR (m): splats whose centre is closer than this are not drawn (see cpr_track.py)
NEAR = float(os.environ.get("NEAR", 0.01))
ply, video, poses_path, out_path = sys.argv[1:5]
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
BINS = [(0, 5), (5, 15), (15, 40), (40, 1e9)]
P = json.load(open(poses_path))["poses"]; STEP, FIRST = int(os.environ.get("STEP", 10)), int(os.environ.get("FIRST", 0))
LAST = int(os.environ.get("LAST", len(P) - 1))
cap = cv2.VideoCapture(video); res = []
for i in range(FIRST, LAST + 1, STEP):
    if not P[i] or P[i]["src"] not in SOLVED: continue
    cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, fr = cap.read()
    if not ok: break
    q = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), (RW, RH), interpolation=cv2.INTER_AREA)
    rgb, dep, al, vm = render([(Rot.from_quat(P[i]["quat"]), np.array(P[i]["pos"]))])
    with torch.no_grad(): o = inference([(view(q, 0), view(rgb[0], 1))], model, dev, batch_size=1, verbose=False)
    a, b = fast_reciprocal_NNs(o["pred1"]["desc"][0], o["pred2"]["desc"][0], subsample_or_initxy1=SUB, device=dev, dist="dot", block_size=2**13)
    a, b = np.asarray(a), np.asarray(b); d = dep[0][b[:, 1], b[:, 0]]
    k = qvalid[a[:, 1], a[:, 0]] & (al[0][b[:, 1], b[:, 0]] > 0.9) & (d > 0.3)
    a, b, d = a[k], b[k], d[k]; disp = np.linalg.norm(a - b, axis=1) / s              # full-resolution pixels
    rec = {"i": i, "src": P[i]["src"], "n": int(len(a)), "bins": {}}
    for lo, hi in BINS:
        m = (d >= lo) & (d < hi)
        if m.sum() >= 5: rec["bins"][f"{lo}-{hi if hi < 1e9 else 'inf'}"] = [int(m.sum()), round(float(np.median(disp[m])), 2)]
    res.append(rec)
json.dump(res, open(out_path, "w"))
for key in [f"{lo}-{hi if hi < 1e9 else 'inf'}" for lo, hi in BINS]:
    v_ = [r["bins"][key][1] for r in res if key in r["bins"]]
    if v_: print(f"depth {key:>7} m: {len(v_):4d} frames, per-frame median displacement p50 {np.median(v_):5.2f} p90 {np.percentile(v_, 90):5.2f} px")
far = [max(r["bins"].get("15-40", [0, 0])[1], r["bins"].get("40-inf", [0, 0])[1]) for r in res]
print(f"{len(res)} frames; frames whose far points (>15 m) sit over 5 px off: {sum(f > 5 for f in far)}, over 10 px: {sum(f > 10 for f in far)}")
