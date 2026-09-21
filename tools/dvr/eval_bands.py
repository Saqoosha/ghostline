"""Where does the photometric loss come from? For given frames, render the init and the refined pose
at the fine level and report the loss per horizontal band (top: far objects and tree line; middle;
bottom: grass), same loss as refine.py. A refined pose that wins on the grass band and loses on the
top band has been pulled by the ground texture.
usage: eval_bands.py ply video init.json refined.json i1,i2,..."""
import json, sys, math, numpy as np, torch, cv2
from plyfile import PlyData
from gsplat import rasterization
import os; DUMP = os.environ.get("DUMP"); DUMP and os.makedirs(DUMP, exist_ok=True)
dev = "cuda"; ply, video, init_json, ref_json = sys.argv[1:5]; idx = [int(x) for x in (open(sys.argv[5][1:]).read() if sys.argv[5].startswith("@") else sys.argv[5]).split(",")]
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
fk = cam["source_fisheye"]; Kf = np.array([[fk["fx"], 0, fk["cx"]], [0, fk["fy"], fk["cy"]], [0, 0, 1]]); Df = np.array(fk["k"])
Kn = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
m1, m2 = cv2.fisheye.initUndistortRectifyMap(Kf, Df, np.eye(3), Kn, (W, H), cv2.CV_16SC2)
osd = np.full((H, W), 255, np.uint8); osd[:82] = 0; osd[632:] = 0
valid_full = cv2.erode(cv2.remap(osd, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0), np.ones((9, 9), np.uint8))
w, h = int(W * SCALE), int(H * SCALE)
K = torch.tensor([[cam["fx"] * SCALE, 0, cam["cx"] * SCALE], [0, cam["fy"] * SCALE, cam["cy"] * SCALE], [0, 0, 1]], dtype=torch.float32, device=dev)[None]
valid = cv2.resize(valid_full, (w, h), interpolation=cv2.INTER_NEAREST) > 0
def sky_mask(rgb):
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV); hh, ss, vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    sky = ((vv > 140) & (ss < 70)) | ((hh > 95) & (hh < 130) & (vv > 100))
    return cv2.dilate(sky.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
def feats(img, mask):
    g = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]; gn = (g - g[mask].mean()) / (g[mask].std() + 1e-6)
    return gn, gn[:, 1:] - gn[:, :-1], gn[1:, :] - gn[:-1, :]
def loss(ren, alpha, tgt, mask, norm):                 # normalised over `norm` (the whole frame), averaged over `mask`
    gn, gx, gy = feats(ren, norm); tn, tx, ty = tgt; m = mask & (alpha > 0.5)
    if m.sum() < 100: return float("nan")
    return ((gn - tn)[m].abs().mean() + 2.0 * ((gx - tx)[m[:, 1:]].abs().mean() + (gy - ty)[m[1:, :]].abs().mean())).item()
def qR(q):
    x, y, z, wq = q; return np.array([[1-2*(y*y+z*z), 2*(x*y-z*wq), 2*(x*z+y*wq)], [2*(x*y+z*wq), 1-2*(x*x+z*z), 2*(y*z-x*wq)], [2*(x*z-y*wq), 2*(y*z+x*wq), 1-2*(x*x+y*y)]])
def render(p):
    R = qR(p["quat"]); C = np.array(p["pos"]); w2c = np.eye(4); w2c[:3, :3] = R.T; w2c[:3, 3] = -R.T @ C
    img, alpha, _ = rasterization(means, quats, scales, opac, colors, torch.tensor(w2c, dtype=torch.float32, device=dev)[None], K, w, h, sh_degree=SH)
    return img[0], alpha[0, ..., 0]
BL = float(os.environ.get("BLUR", 0))
import torch.nn.functional as F_
def blur(im):
    if BL <= 0: return im
    k = int(BL * 3) * 2 + 1; g1 = torch.exp(-(torch.arange(k, device=dev, dtype=torch.float32) - k // 2) ** 2 / (2 * BL * BL)); g1 = g1 / g1.sum()
    x = im.permute(2, 0, 1)[None]; x = F_.conv2d(x, g1.view(1, 1, 1, k).expand(3, 1, 1, k), padding=(0, k // 2), groups=3); x = F_.conv2d(x, g1.view(1, 1, k, 1).expand(3, 1, k, 1), padding=(k // 2, 0), groups=3); return x[0].permute(1, 2, 0)
PI = json.load(open(init_json))["poses"]
if ref_json.endswith(".jsonl"):                  # raw refine records: the refined pose where accepted, else init
    PR = list(PI)
    for l in open(ref_json):
        r = json.loads(l); PR[r["i"]] = {**PI[r["i"]], "pos": r["pos"], "quat": r["quat"], "src": "refined" if r["accepted"] else "rejected"}
else: PR = json.load(open(ref_json))["poses"]
cap = cv2.VideoCapture(video)
bands = {"top": (0, h // 4), "mid": (h // 4, h // 2), "low": (h // 2, 3 * h // 4), "bot": (3 * h // 4, h), "all": (0, h)}
print("   i  src        " + "  ".join(f"{b:>5s}(init->ref)" for b in bands))
with torch.no_grad():
    for i in idx:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, fr = cap.read(); fr = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
        sky = cv2.resize(sky_mask(fr).astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
        f = torch.tensor(cv2.resize(fr, (w, h), interpolation=cv2.INTER_AREA), dtype=torch.float32, device=dev) / 255
        base = torch.tensor(valid & ~sky, device=dev); tgt = feats(blur(f), base)
        ri, ai = render(PI[i]); rr, ar = render(PR[i]); ri, rr = blur(ri), blur(rr)
        row = []
        for b, (y0, y1) in bands.items():
            m = torch.zeros_like(base); m[y0:y1] = base[y0:y1]
            row.append(f"{loss(ri, ai, tgt, m, base):.3f}->{loss(rr, ar, tgt, m, base):.3f}")
        print(f"{i:4d}  {PR[i]['src']:8s} " + "  ".join(f"{r:>17s}" for r in row), flush=True)
        if DUMP:                                   # DVR | render at init | render at refined, fine level
            trip = np.concatenate([(f.cpu().numpy() * 255).astype(np.uint8), (ri.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8), (rr.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)], axis=1)
            cv2.imwrite(f"{DUMP}/cmp_{i:05d}.jpg", cv2.cvtColor(trip, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
