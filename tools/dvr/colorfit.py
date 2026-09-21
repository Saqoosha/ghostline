"""Fit a colour transform 3DGS -> DVR: render the scene at anchored DVR poses, pair pixels with the
DVR frame (masked: OSD, border, sky, alpha < 0.9), and solve rgb_dvr = M rgb_render + b by robust
least squares. Writes colorfit.json with M (3x3) and b, in the 0..1 colour space both images share."""
import json, sys, os, numpy as np, torch, cv2
from plyfile import PlyData
from gsplat import rasterization
dev = "cuda"; ply, video, poses_json, out = sys.argv[1:5]; EVERY = int(sys.argv[5]) if len(sys.argv) > 5 else 40
v = PlyData.read(ply)["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.1]
means = torch.tensor(np.c_[v["x"], v["y"], v["z"]], dtype=torch.float32, device=dev)
quats = torch.tensor(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], dtype=torch.float32, device=dev)
scales = torch.exp(torch.tensor(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]], dtype=torch.float32, device=dev))
opac = torch.sigmoid(torch.tensor(v["opacity"], dtype=torch.float32, device=dev))
sh0 = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :]
rest = np.stack([np.c_[[v[f"f_rest_{c*15+k}"] for k in range(15)]].T for c in range(3)], axis=2)
colors = torch.tensor(np.concatenate([sh0, rest], axis=1), dtype=torch.float32, device=dev)
cam = json.load(open(video + ".json")); W, H = cam["width"], cam["height"]; s = 0.5; w, h = int(W * s), int(H * s)
K = torch.tensor([[cam["fx"] * s, 0, cam["cx"] * s], [0, cam["fy"] * s, cam["cy"] * s], [0, 0, 1]], dtype=torch.float32, device=dev)[None]
fk = cam["source_fisheye"]; Kf = np.array([[fk["fx"], 0, fk["cx"]], [0, fk["fy"], fk["cy"]], [0, 0, 1]]); Df = np.array(fk["k"])
Kn = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
m1, m2 = cv2.fisheye.initUndistortRectifyMap(Kf, Df, np.eye(3), Kn, (W, H), cv2.CV_16SC2)
osd = np.full((H, W), 255, np.uint8); osd[:82] = 0; osd[632:] = 0
valid = cv2.resize(cv2.erode(cv2.remap(osd, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0), np.ones((15, 15), np.uint8)), (w, h), interpolation=cv2.INTER_NEAREST) > 0
def q2R(q):
    x, y, z, wq = q; return np.array([[1-2*(y*y+z*z), 2*(x*y-z*wq), 2*(x*z+y*wq)], [2*(x*y+z*wq), 1-2*(x*x+z*z), 2*(y*z-x*wq)], [2*(x*z-y*wq), 2*(y*z+x*wq), 1-2*(x*x+y*y)]])
poses = json.load(open(poses_json))["poses"]; cap = cv2.VideoCapture(video); A, B = [], []
for i, p in enumerate(poses):
    if p is None or p["src"] != "kept" or i % EVERY: continue
    cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, fr = cap.read()
    if not ok: break
    fr = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    hsv = cv2.cvtColor((fr * 255).astype(np.uint8), cv2.COLOR_RGB2HSV); sky = ((hsv[..., 2] > 140) & (hsv[..., 1] < 70)) | ((hsv[..., 0] > 95) & (hsv[..., 0] < 130) & (hsv[..., 2] > 100))
    R = q2R(p["quat"]); C = np.array(p["pos"]); w2c = np.eye(4); w2c[:3, :3] = R.T; w2c[:3, 3] = -R.T @ C
    with torch.no_grad():
        img, alpha, _ = rasterization(means, quats, scales, opac, colors, torch.tensor(w2c, dtype=torch.float32, device=dev)[None], K, w, h, sh_degree=3)
    ren = img[0].clamp(0, 1).cpu().numpy(); al = alpha[0, ..., 0].cpu().numpy()
    lum_r = ren @ np.array([0.299, 0.587, 0.114]); lum_f = fr @ np.array([0.299, 0.587, 0.114])
    grey_r = (ren.max(2) - ren.min(2)) < 0.08; grey_f = (fr.max(2) - fr.min(2)) < 0.08
    m = valid & ~cv2.dilate(sky.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool) & (al > 0.9)
    m &= ~((lum_r > 0.75) & grey_r) & ~((lum_f > 0.75) & grey_f)          # sky/cloud on either side: bright and colourless
    # blur both a little: the pose is not pixel-exact, so compare local means rather than pixels
    rb, fb = cv2.GaussianBlur(ren, (0, 0), 3), cv2.GaussianBlur(fr, (0, 0), 3)
    A.append(rb[m][::7]); B.append(fb[m][::7])
A, B = np.concatenate(A), np.concatenate(B); print(f"{len(A):,} pixel pairs from {len(set([i for i,p in enumerate(poses) if p and p['src']=='kept' and i%EVERY==0]))} frames")
np.savez_compressed(out.replace('.json', '_pairs.npz'), A=A[::3].astype(np.float16), B=B[::3].astype(np.float16))
X = np.c_[A, np.ones(len(A))]; wgt = np.ones(len(A))
for it in range(5):                                             # IRLS with a Cauchy weight
    Mb, *_ = np.linalg.lstsq(X * wgt[:, None], B * wgt[:, None], rcond=None)
    r = np.linalg.norm(X @ Mb - B, axis=1); wgt = 1 / np.sqrt(1 + (r / 0.08) ** 2)
M, b = Mb[:3].T, Mb[3]
before = np.abs(A - B).mean(0); after = np.abs(X @ Mb - B).mean(0)
print("M =\n", M.round(4), "\nb =", b.round(4)); print("mean |render - dvr| per channel before", before.round(4), "after", after.round(4))
print("mean colour: render", A.mean(0).round(3), " dvr", B.mean(0).round(3), " render after fit", (X @ Mb).mean(0).round(3))
json.dump({"M": M.tolist(), "b": b.tolist(), "pairs": int(len(A)), "mae_before": before.tolist(), "mae_after": after.tolist()}, open(out, "w"), indent=1)
