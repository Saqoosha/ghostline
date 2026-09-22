"""refine.py, batched: B frames per rasterization call (gsplat takes viewmats [C,4,4]) and the video
read in order instead of seeking. Same model, loss, prior, acceptance and JSONL record as refine.py,
so refined2poses.py reads the output unchanged. Each frame's se(3) delta is its own parameter row;
the losses are summed, so Adam's per-element state keeps every frame independent.

env: as refine.py, plus BATCH (frames per call, default 16)."""
import json, sys, time, math, os, numpy as np, torch, cv2
from plyfile import PlyData
from gsplat import rasterization
dev = "cuda"
# WDDM lets CUDA spill into system memory instead of failing: a render with the camera inside the
# grass or a flag (a translation-search candidate 2 m lower) filled the 24 GB and the job sat at
# 100 % GPU for an hour doing nothing (run 11a, 2026-09-22). Cap the allocator so it raises OOM,
# and skip the candidate.
torch.cuda.set_per_process_memory_fraction(0.85)
ply, video, poses_json, out_path = sys.argv[1:5]
stride = int(sys.argv[5]) if len(sys.argv) > 5 else 1
LIMIT = int(os.environ.get("LIMIT", 0))                      # stop after this many frames (timing runs)
ITERS, SCALE = int(os.environ.get("ITERS", 16)), float(os.environ.get("SCALE", 0.5))
SH, OPMIN, B = int(os.environ.get("SH", 1)), float(os.environ.get("OPMIN", 0.1)), int(os.environ.get("BATCH", 16))
ONLY_INTERP = os.environ.get("ONLY_INTERP") == "1"
FRAMES = [int(x) for x in (open(os.environ["FRAMES"][1:]).read() if os.environ["FRAMES"].startswith("@") else os.environ["FRAMES"]).split(",")] if os.environ.get("FRAMES") else None   # explicit frame list, or @file
# BANDS=n: the loss is the mean over n horizontal bands of each band's mean, instead of one mean over
# all pixels. Grass is two thirds of the frame; at #182-#188 the plain mean let the grass rows improve
# (1.7 -> 1.4) while the horizon rows, the only ones that pin the rotation, got worse (3.1 -> 4.0).
BANDS = int(os.environ.get("BANDS", 0))
# EDGE=sigma: weight every pixel by the DVR frame's large-scale edge strength (gradient magnitude of
# the gray blurred with this sigma, at the fine level, scaled by its 95th percentile). Flags, pylons,
# gates, the tree line and the horizon keep their weight; grass texture, which is finer than the blur
# and motion-blurred differently from the sharp render, loses it. #184's pylon stayed 3-4 deg off
# under both the plain and the band loss while the loss fell.
EDGE = float(os.environ.get("EDGE", 0))
# The descent's basin at 240x180 is a few pixels; #184 needed ~3 deg of yaw = 14 px there and the
# optimiser never left the init's basin under any loss. COARSE=3 adds a 120x90 level first;
# SEARCH=d evaluates the coarsest loss on a yaw/pitch grid of +-d deg (1 deg steps, no gradients)
# per frame and starts the descent from the best cell.
COARSE, SEARCH = int(os.environ.get("COARSE", 2)), float(os.environ.get("SEARCH", 0))
# TSEARCH=d: grid over the camera-frame translation dx, dz in [-d, d] (1 m steps) and dy in {-2, 0, 2}
# at the coarsest level, before the descent. The descent moves a pose ~1 m at most; #915 sat 6 m
# left of where the DVR put it (a pylon turn interpolated straight through the flag), with the
# loss falling monotonically toward the true spot (0.896 -> 0.746 at +6 m). The prior and the
# acceptance limit are measured from the searched start, not the init (MAX_TR env, default 1.5).
TSEARCH = float(os.environ.get("TSEARCH", 0))
# BLUR=sigma (px at the fine level, scaled per level): Gaussian-blur both the DVR frame and the
# render before the loss. Measured on #184 (yaw sweep, band loss): unblurred, the horizon band is
# flat (3.23-3.39 over +-6 deg, no minimum) and the grass bands fall monotonically to one side, so
# the pose drifts with the grass; at sigma 3 the horizon band is a bowl with its minimum 2 deg from
# the init and the grass bands are flat. Grass texture is finer than the blur, flags and gates are not.
BLUR = float(os.environ.get("BLUR", 0))
import torch.nn.functional as F_
def gauss_blur(img, sigma):                        # img (B,h,w,3), separable, differentiable
    if sigma <= 0: return img
    k = int(sigma * 3) * 2 + 1; g1 = torch.exp(-(torch.arange(k, device=dev, dtype=torch.float32) - k // 2) ** 2 / (2 * sigma * sigma)); g1 = g1 / g1.sum()
    x = img.permute(0, 3, 1, 2)
    x = F_.conv2d(x, g1.view(1, 1, 1, k).expand(3, 1, 1, k), padding=(0, k // 2), groups=3)
    x = F_.conv2d(x, g1.view(1, 1, k, 1).expand(3, 1, k, 1), padding=(k // 2, 0), groups=3)
    return x.permute(0, 2, 3, 1)
PRIOR, SIG_R, SIG_T = 0.1, math.radians(6.0), 1.0
MAX_ROT, MAX_TR = 10.0, float(os.environ.get("MAX_TR", 1.5))
# ---- scene
v = PlyData.read(ply)["vertex"].data
if OPMIN > 0: v = v[1 / (1 + np.exp(-v["opacity"])) >= OPMIN]
means = torch.tensor(np.c_[v["x"], v["y"], v["z"]], dtype=torch.float32, device=dev)
quats = torch.tensor(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], dtype=torch.float32, device=dev)
scales = torch.exp(torch.tensor(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]], dtype=torch.float32, device=dev))
opac = torch.sigmoid(torch.tensor(v["opacity"], dtype=torch.float32, device=dev))
sh0 = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :]
rest = np.stack([np.c_[[v[f"f_rest_{c*15+k}"] for k in range(15)]].T for c in range(3)], axis=2)
colors = torch.tensor(np.concatenate([sh0, rest], axis=1)[:, :(SH + 1) ** 2], dtype=torch.float32, device=dev)
print(f"scene {len(v):,} splats (opacity >= {OPMIN}), sh {SH}, iters {ITERS}, scale {SCALE}, batch {B}", flush=True)
# ---- camera, static masks, two render levels
cam = json.load(open(video + ".json")); W, H = cam["width"], cam["height"]
fk = cam["source_fisheye"]; Kf = np.array([[fk["fx"], 0, fk["cx"]], [0, fk["fy"], fk["cy"]], [0, 0, 1]]); Df = np.array(fk["k"])
Kn = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
m1, m2 = cv2.fisheye.initUndistortRectifyMap(Kf, Df, np.eye(3), Kn, (W, H), cv2.CV_16SC2)
osd = np.full((H, W), 255, np.uint8); osd[:82] = 0; osd[632:] = 0
valid_full = cv2.erode(cv2.remap(osd, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0), np.ones((9, 9), np.uint8))
class Level:
    def __init__(self, s):
        self.w, self.h = int(W * s), int(H * s)
        self.K = torch.tensor([[cam["fx"] * s, 0, cam["cx"] * s], [0, cam["fy"] * s, cam["cy"] * s], [0, 0, 1]], dtype=torch.float32, device=dev)
        self.valid = cv2.resize(valid_full, (self.w, self.h), interpolation=cv2.INTER_NEAREST) > 0
levels = [Level(SCALE / 2 ** k) for k in range(COARSE - 1, -1, -1)]
print("render " + " then ".join(f"{L.w}x{L.h}" for L in levels) + f", valid pixels {valid_full.mean()/255*100:.0f}%, search +-{SEARCH} deg", flush=True)
def sky_mask(rgb_full):
    hsv = cv2.cvtColor(rgb_full, cv2.COLOR_RGB2HSV); hh, ss, vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    sky = ((vv > 140) & (ss < 70)) | ((hh > 95) & (hh < 130) & (vv > 100))
    return cv2.dilate(sky.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
# ---- batched helpers: leading dim is the frame
def se3_exp(xi):                                   # xi: (B,6) -> (B,4,4)
    w_, u = xi[:, :3], xi[:, 3:]; th = torch.linalg.norm(w_, dim=1, keepdim=True)[..., None] + 1e-12   # B,1,1
    z = torch.zeros_like(w_[:, 0])
    Kx = torch.stack([torch.stack([z, -w_[:, 2], w_[:, 1]], 1), torch.stack([w_[:, 2], z, -w_[:, 0]], 1), torch.stack([-w_[:, 1], w_[:, 0], z], 1)], 1)
    I = torch.eye(3, device=dev)[None]; KK = Kx @ Kx
    R = I + torch.sin(th) / th * Kx + (1 - torch.cos(th)) / th**2 * KK
    V = I + (1 - torch.cos(th)) / th**2 * Kx + (th - torch.sin(th)) / th**3 * KK
    M = torch.eye(4, device=dev)[None].repeat(xi.shape[0], 1, 1).clone()
    M[:, :3, :3] = R; M[:, :3, 3] = (V @ u[..., None])[..., 0]; return M
def render(viewmats, L):                           # (B,4,4) -> img (B,h,w,3), alpha (B,h,w)
    img, alpha, _ = rasterization(means, quats, scales, opac, colors, viewmats, L.K[None].expand(viewmats.shape[0], 3, 3), L.w, L.h, sh_degree=SH)
    return img, alpha[..., 0]
def gray_feats(img, mask):                         # per-frame normalized gray + gradients; mask (B,h,w) bool
    g = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
    mf = mask.float(); n = mf.sum((1, 2)).clamp(min=1)
    mean = (g * mf).sum((1, 2)) / n; var = (((g - mean[:, None, None]) ** 2) * mf).sum((1, 2)) / n
    gn = (g - mean[:, None, None]) / (var.sqrt()[:, None, None] + 1e-6)
    return gn, gn[:, :, 1:] - gn[:, :, :-1], gn[:, 1:, :] - gn[:, :-1, :]
def masked_mean(x, m, w=None):                     # per frame; with BANDS, the mean of the band means; w: pixel weights
    mf = m.float() if w is None else m.float() * w
    if not BANDS: return (x.abs() * mf).sum((1, 2)) / mf.sum((1, 2)).clamp(min=1e-6)
    tot, cnt, h = 0, 0, x.shape[1]
    for b in range(BANDS):
        mb = mf[:, b * h // BANDS:(b + 1) * h // BANDS]; n = mb.sum((1, 2)); ok = (m[:, b * h // BANDS:(b + 1) * h // BANDS].sum((1, 2)) > 100).float()
        tot = tot + ok * (x[:, b * h // BANDS:(b + 1) * h // BANDS].abs() * mb).sum((1, 2)) / n.clamp(min=1e-6); cnt = cnt + ok
    return tot / cnt.clamp(min=1)
def edge_weight(gray_u8, mask, s):                 # (h,w) uint8 gray -> (h,w) float weight in 0..1
    gb = cv2.GaussianBlur(gray_u8.astype(np.float32), (0, 0), s)
    mag = np.hypot(cv2.Sobel(gb, cv2.CV_32F, 1, 0), cv2.Sobel(gb, cv2.CV_32F, 0, 1))
    ref = np.percentile(mag[mask], 95) if mask.any() else 1.0
    return np.clip(mag / max(ref, 1e-6), 0, 1).astype(np.float32)
def loss_fn(ren, alpha, tgt, mask, w=None, sigma=0):   # -> (B,)
    gn, gx, gy = gray_feats(gauss_blur(ren, sigma), mask); tn, tx, ty = tgt; m = mask & (alpha > 0.5)
    wx = None if w is None else torch.minimum(w[:, :, 1:], w[:, :, :-1]); wy = None if w is None else torch.minimum(w[:, 1:, :], w[:, :-1, :])
    return masked_mean(gn - tn, m, w) + 2.0 * (masked_mean(gx - tx, m[:, :, 1:], wx) + masked_mean(gy - ty, m[:, 1:, :], wy))
def qxyzw_to_R(q):
    x, y, z, wq = q; return np.array([[1-2*(y*y+z*z), 2*(x*y-z*wq), 2*(x*z+y*wq)], [2*(x*y+z*wq), 1-2*(x*x+z*z), 2*(y*z-x*wq)], [2*(x*z-y*wq), 2*(y*z+x*wq), 1-2*(x*x+y*y)]])
def R_to_q(R):
    tr = np.trace(R)
    if tr > 0: s_ = math.sqrt(tr + 1) * 2; return [(R[2,1]-R[1,2])/s_, (R[0,2]-R[2,0])/s_, (R[1,0]-R[0,1])/s_, 0.25*s_]
    i = int(np.argmax(np.diag(R))); j, k = (i+1) % 3, (i+2) % 3; s_ = math.sqrt(1 + R[i,i] - R[j,j] - R[k,k]) * 2
    q = [0, 0, 0, 0]; q[i] = 0.25*s_; q[j] = (R[j,i]+R[i,j])/s_; q[k] = (R[k,i]+R[i,k])/s_; q[3] = (R[k,j]-R[j,k])/s_; return q
# ---- frames, read in order
P = json.load(open(poses_json)); poses = P["poses"]
out = open(out_path, "a"); done = set()
try:
    for line in open(out_path): done.add(json.loads(line)["i"])
except FileNotFoundError: pass
ONLY_SRC = os.environ.get("ONLY_SRC", "").split(",") if os.environ.get("ONLY_SRC") else None      # e.g. ONLY_SRC=kept: just the COLMAP anchors
todo = [i for i, p in enumerate(poses) if p is not None and not i % stride and i not in done and not (ONLY_INTERP and p["src"] in ("kept", "ground")) and (ONLY_SRC is None or p["src"] in ONLY_SRC)]
if FRAMES: todo = [i for i in FRAMES if poses[i] is not None and poses[i]["src"] != "ground"]
if LIMIT: todo = todo[:LIMIT]
print(f"{len(todo)} frames to refine", flush=True)
cap = cv2.VideoCapture(video); at = 0                # index of the next frame cap.read() returns
def read_frame(i):
    global at
    if i < at: cap.set(cv2.CAP_PROP_POS_FRAMES, i); at = i
    while at < i: cap.grab(); at += 1
    ok, fr = cap.read(); at += 1
    return cv2.cvtColor(fr, cv2.COLOR_BGR2RGB) if ok else None
t_start, n_done = time.time(), 0
for b0 in range(0, len(todo), B):
    idx = todo[b0:b0 + B]; frames = []
    for i in idx:
        fr = read_frame(i)
        if fr is None: break
        frames.append(fr)
    idx = idx[:len(frames)]
    if not idx: break
    n = len(idx); per = []
    for L in levels:
        fs = np.stack([cv2.resize(fr, (L.w, L.h), interpolation=cv2.INTER_AREA) for fr in frames])
        sk = np.stack([cv2.resize(sky_mask(fr).astype(np.uint8), (L.w, L.h), interpolation=cv2.INTER_NEAREST) > 0 for fr in frames])
        m = torch.tensor(L.valid[None] & ~sk, device=dev)
        w = None
        if EDGE:
            sig = EDGE * L.w / levels[-1].w        # the same blur in frame units at every level
            w = torch.tensor(np.stack([edge_weight(cv2.cvtColor(f_, cv2.COLOR_RGB2GRAY), mk, sig) for f_, mk in zip(fs, L.valid[None] & ~sk)]), device=dev)
        L.sigma = BLUR * L.w / levels[-1].w
        per.append((gray_feats(gauss_blur(torch.tensor(fs, dtype=torch.float32, device=dev) / 255, L.sigma), m), m, w))
    Rc2w = [qxyzw_to_R(poses[i]["quat"]) for i in idx]; C = [np.array(poses[i]["pos"]) for i in idx]
    base = torch.tensor(np.stack([np.r_[np.c_[R.T, -R.T @ c], [[0, 0, 0, 1]]] for R, c in zip(Rc2w, C)]), dtype=torch.float32, device=dev)
    xi0 = torch.zeros(n, 6, device=dev)
    if SEARCH or TSEARCH:                          # grid at the coarsest level: rotation (pitch, yaw) and/or camera-frame translation
        with torch.no_grad():
            L0 = levels[0]; tgt0, m0, w0 = per[0]; bestL = None
            rots = [(dp, dy) for dp in np.arange(-SEARCH, SEARCH + 0.5, 1.0) for dy in np.arange(-SEARCH, SEARCH + 0.5, 1.0)] if SEARCH else [(0.0, 0.0)]
            trans = [(tx, ty, tz) for ty in (-2.0, 0.0, 2.0) for tz in np.arange(-TSEARCH, TSEARCH + 0.5, 1.0) for tx in np.arange(-TSEARCH, TSEARCH + 0.5, 1.0)] if TSEARCH else [(0.0, 0.0, 0.0)]
            ymin = torch.tensor([poses[i]["pos"][1] for i in idx], device=dev)   # world y of the init (ground is ~1.5 in this scene)
            for dp, dy in rots:
                for tx, ty, tz in trans:
                    cand = torch.zeros(n, 6, device=dev); cand[:, 0] = math.radians(dp); cand[:, 1] = math.radians(dy)
                    # camera-frame translation of the camera by (tx,ty,tz) = w2c translation of -(tx,ty,tz)
                    cand[:, 3] = -tx; cand[:, 4] = -ty; cand[:, 5] = -tz
                    if ty < 0:                     # never search below 2.5 m world y: the camera lands in the grass
                        cand[:, 4] = torch.where(ymin + ty < 2.5, torch.zeros_like(cand[:, 4]), cand[:, 4])
                    try:
                        r_, a_ = render(se3_exp(cand) @ base, L0); lc = loss_fn(r_, a_, tgt0, m0, w0, L0.sigma)
                    except torch.cuda.OutOfMemoryError:
                        torch.cuda.empty_cache(); continue
                    if bestL is None: bestL = lc.clone(); xi0 = cand
                    else: better = lc < bestL; bestL = torch.where(better, lc, bestL); xi0 = torch.where(better[:, None], cand, xi0)
    xi = xi0.clone().requires_grad_(True); opt = torch.optim.Adam([xi], lr=0.01)
    with torch.no_grad(): ri, ai = render(base, levels[-1]); li = loss_fn(ri, ai, *per[-1], levels[-1].sigma)  # (tgt, mask, weight)
    best, best_xi = li.clone(), xi0.clone()
    for it in range(ITERS):
        lvl = min(len(levels) - 1, it * 3 // ITERS) if len(levels) == 3 else (0 if it < ITERS // 3 else 1)
        L = levels[lvl]; tgt, m, w = per[lvl]
        for g in opt.param_groups: g["lr"] = 0.01 * (0.25 ** (it / ITERS))
        opt.zero_grad()
        try: ren, alpha = render(se3_exp(xi) @ base, L)
        except torch.cuda.OutOfMemoryError: torch.cuda.empty_cache(); print(f"  OOM in descent at batch starting #{idx[0]}, keeping the best so far", flush=True); break
        dxi = xi - xi0                             # the prior holds the pose near the (searched) start, not the init
        prior = PRIOR * ((dxi[:, :3] / SIG_R).pow(2).sum(1) + (dxi[:, 3:] / SIG_T).pow(2).sum(1))
        lb = loss_fn(ren, alpha, tgt, m, w, L.sigma) + prior; lb.sum().backward(); opt.step()
        if lvl == len(levels) - 1:
            with torch.no_grad():
                better = lb < best; best = torch.where(better, lb, best); best_xi[better] = xi.detach()[better]
    M = (se3_exp(best_xi) @ base).detach().cpu().numpy(); bx = best_xi.cpu().numpy(); bl = best.cpu().numpy(); l0 = li.cpu().numpy()
    for k, i in enumerate(idx):
        Rw = M[k, :3, :3].T; Cw = -Rw @ M[k, :3, 3]
        d_rot = float(np.degrees(np.linalg.norm(bx[k, :3]))); d_tr = float(np.linalg.norm(bx[k, 3:]))
        accepted = bl[k] < l0[k] * 0.99 and d_rot <= MAX_ROT and d_tr <= MAX_TR
        rec = {"i": i, "t": poses[i]["t"], "pos": np.round(Cw, 3).tolist(), "quat": np.round(R_to_q(Rw), 6).tolist(), "loss_init": round(float(l0[k]), 4), "loss_warm": round(float(l0[k]), 4), "loss": round(float(bl[k]), 4),
               "shift_m": round(float(np.linalg.norm(Cw - C[k])), 3), "rot_deg": round(float(np.degrees(np.arccos(np.clip((np.trace(Rc2w[k].T @ Rw) - 1) / 2, -1, 1)))), 2), "src": poses[i]["src"], "accepted": bool(accepted)}
        out.write(json.dumps(rec) + "\n")
    out.flush(); n_done += n
    print(f"{n_done} frames, {(time.time()-t_start)/n_done:.2f} s/frame, last: i={idx[-1]} loss {l0[-1]:.3f}->{bl[-1]:.3f} shift {rec['shift_m']} m rot {rec['rot_deg']} deg", flush=True)
print("REFINE-DONE", flush=True)
