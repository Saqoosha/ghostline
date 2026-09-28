"""Fit a colour transform from the 3DGS render to the DVR, and optionally bake it into the ply.
On d07 the colour-tweaked scene tracked 851 of 1,063 frames against 683 for the original, and its path was better by
either scene's measure: the closer the scene looks to the DVR, the better MASt3R matches it. This measures how far
it still is. For each flight, FRAMES frames solved on their own points are rendered at their solved pose; both images
are blurred (the pose is good to a few pixels, not to one) and every pixel the render covers (alpha > 0.95, clear of
the OSD bands and the undistortion rim) is a pair render rgb -> DVR rgb. A 3x4 affine (3x3 matrix and an offset) is
fitted per flight with a Huber loss, and one over all flights (MODE=ls; the default MODE=mk matches the
colour distributions instead - see fit_mk).
The render is a weighted sum of splat colours with weights summing to the alpha, so on covered pixels an affine map
of every splat's colour is the same map of the render. Baking (OUT=): colour = 0.5 + C0 f_dc, so f_dc' = (M(0.5 + C0
f_dc) + b - 0.5) / C0, and each higher SH band of the three channels is multiplied by M (they carry no offset).
usage (mastenv, with gsplat): color_fit.py scene.ply <flight dir>:<poses.json> [...]   env: FRAMES (40), OUT (ply to
write with the all-flight fit, or with FIT=<flight dir> for one flight's)"""
import json, os, sys, numpy as np, torch, cv2
from plyfile import PlyData, PlyElement
from gsplat import rasterization
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from frustum import rasterize_culled   # centre-in-view culling, as PlayCanvas
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation as Rot
dev = "cuda"; FRAMES = int(os.environ.get("FRAMES", 40)); C0 = 0.28209479177387814
ply = sys.argv[1]; flights = [a.split(":") for a in sys.argv[2:]]
pd = PlyData.read(ply); v = pd["vertex"].data; keep = 1 / (1 + np.exp(-v["opacity"])) >= 0.1; vk = v[keep]
T = lambda a: torch.tensor(a, dtype=torch.float32, device=dev)
means, quats = T(np.c_[vk["x"], vk["y"], vk["z"]]), T(np.c_[vk["rot_0"], vk["rot_1"], vk["rot_2"], vk["rot_3"]])
scales, opac = torch.exp(T(np.c_[vk["scale_0"], vk["scale_1"], vk["scale_2"]])), torch.sigmoid(T(vk["opacity"]))
sh0 = np.c_[vk["f_dc_0"], vk["f_dc_1"], vk["f_dc_2"]][:, None, :]; rest = np.stack([np.c_[[vk[f"f_rest_{c*15+k}"] for k in range(15)]].T for c in range(3)], axis=2)
colors = T(np.concatenate([sh0, rest], axis=1)[:, :4])
RW, RH = 320, 240
def pairs(fdir, pfile):
    cam = json.load(open(f"{fdir}/dvr_pinhole.mp4.json")); W, H = cam["width"], cam["height"]; s = RW / W
    K = T([[cam["fx"] * s, 0, cam["cx"] * s], [0, cam["fy"] * s, cam["cy"] * s], [0, 0, 1]])
    fk = cam["source_fisheye"]; Kf = np.array([[fk["fx"], 0, fk["cx"]], [0, fk["fy"], fk["cy"]], [0, 0, 1]])
    Kp = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
    m1, m2 = cv2.fisheye.initUndistortRectifyMap(Kf, np.array(fk["k"]), np.eye(3), Kp, (W, H), cv2.CV_16SC2)
    osd = np.full((H, W), 255, np.uint8); osd[:82] = 0; osd[632:] = 0
    valid = cv2.resize((cv2.erode(cv2.remap(osd, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT), np.ones((15, 15), np.uint8)) > 0).astype(np.uint8), (RW, RH), interpolation=cv2.INTER_NEAREST) > 0
    P = json.load(open(f"{fdir}/{pfile}"))["poses"]
    ok = [i for i, p in enumerate(P) if p and p["src"] == "ba"]
    pick = set(np.array(ok)[np.linspace(0, len(ok) - 1, FRAMES).round().astype(int)].tolist())
    cap = cv2.VideoCapture(f"{fdir}/dvr_pinhole.mp4"); i = 0; A, B = [], []
    while i <= max(pick):
        good, fr = cap.read()
        if not good: break
        if i in pick:
            R = Rot.from_quat(P[i]["quat"]).as_matrix(); c = np.array(P[i]["pos"]); M = np.eye(4); M[:3, :3] = R.T; M[:3, 3] = -R.T @ c
            with torch.no_grad():
                o, a, _ = rasterize_culled(means, quats, scales, opac, colors, T(M)[None], K[None], RW, RH, sh_degree=1, near_plane=0.01)
            r = o[0, ..., :3].clamp(0, 1).cpu().numpy(); al = a[0, ..., 0].cpu().numpy()
            d = cv2.cvtColor(cv2.resize(fr, (RW, RH), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB).astype(np.float32) / 255
            r, d = cv2.GaussianBlur(r, (0, 0), 2), cv2.GaussianBlur(d, (0, 0), 2)
            m = valid & (al > 0.95); A.append(r[m][::3]); B.append(d[m][::3])
        i += 1
    return np.concatenate(A), np.concatenate(B)
def fit(A, B):
    f = lambda x: ((A @ x[:9].reshape(3, 3).T + x[9:]) - B).ravel()
    x = least_squares(f, np.r_[np.eye(3).ravel(), np.zeros(3)], loss="huber", f_scale=0.05).x
    return x[:9].reshape(3, 3), x[9:]
def fit_mk(A, B):
    # match the colour distribution, not the pixels: the linear map taking A's mean and covariance to B's (Monge-
    # Kantorovich). The pixel regression above shrinks: pairs a few pixels off make a flatter, greyer map fit best
    # (d05: gains 0.37 / 0.58 / 1.67 and offsets of 35-56, a negative diagonal on d07), which would wash the scene out.
    def sq(S): w, V = np.linalg.eigh(S); return V @ np.diag(np.sqrt(np.maximum(w, 1e-12))) @ V.T
    Sa, Sb = np.cov(A.T), np.cov(B.T); Ra = sq(Sa); Ri = np.linalg.inv(Ra)
    M = Ri @ sq(Ra @ Sb @ Ra) @ Ri; return M, B.mean(0) - A.mean(0) @ M.T
if os.environ.get("MODE", "mk") == "mk": fit = fit_mk
def err(A, B, M=np.eye(3), b=np.zeros(3)): return float(np.median(np.abs(A @ M.T + b - B)) * 255)
data = {}
for fdir, pfile in flights:
    A, B = pairs(fdir, pfile); M, b = fit(A, B); data[fdir] = (A, B, M, b)
    print(f"{os.path.basename(fdir):18s} {len(A):7d} px  mean render {np.round(A.mean(0) * 255).astype(int).tolist()} dvr {np.round(B.mean(0) * 255).astype(int).tolist()}"
          f"  |err| p50 {err(A, B):5.1f} -> {err(A, B, M, b):5.1f}  M diag {np.round(np.diag(M), 3).tolist()} b {np.round(b * 255, 1).tolist()}", flush=True)
A = np.concatenate([d[0] for d in data.values()]); B = np.concatenate([d[1] for d in data.values()]); Ma, ba = fit(A, B)
print(f"all flights: |err| p50 {err(A, B):5.1f} -> {err(A, B, Ma, ba):5.1f}\n M {np.round(Ma, 3).tolist()} b {np.round(ba * 255, 1).tolist()}")
for fdir, (A1, B1, M1, b1) in data.items():
    print(f"  {os.path.basename(fdir):18s} with the all-flight fit {err(A1, B1):5.1f} -> {err(A1, B1, Ma, ba):5.1f} (own fit {err(A1, B1, M1, b1):5.1f})")
json.dump({"all": {"M": Ma.tolist(), "b": ba.tolist()}, **{os.path.basename(k): {"M": d[2].tolist(), "b": d[3].tolist()} for k, d in data.items()}},
          open(os.environ.get("FITS", "color_fit.json"), "w"), indent=1)
if os.environ.get("OUT"):
    M, b = (data[os.environ["FIT"]][2], data[os.environ["FIT"]][3]) if os.environ.get("FIT") else (Ma, ba)
    out = v.copy(); dc = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]].astype(np.float64)
    col = (0.5 + C0 * dc) @ M.T + b; dc2 = (col - 0.5) / C0
    for k in range(3): out[f"f_dc_{k}"] = dc2[:, k]
    for j in range(15):                                   # band coefficient j of the three channels, mixed by M
        R3 = np.c_[[v[f"f_rest_{c * 15 + j}"] for c in range(3)]].T.astype(np.float64) @ M.T
        for c in range(3): out[f"f_rest_{c * 15 + j}"] = R3[:, c]
    PlyData([PlyElement.describe(out, "vertex")], byte_order="<").write(os.environ["OUT"])
    print(f"wrote {os.environ['OUT']}")
