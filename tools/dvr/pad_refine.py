"""Refine the hand-placed pad (pad.json, from the viewer's pad tool) by render-and-match on the frames held on the pad.
Before takeoff the camera does not move, so every pad frame sees the same view: render the scan from the current pad
pose, match each of FRAMES pad frames to it with MASt3R (as cpr_track.py), pool the correspondences of all of them and
solve one pose by PnP; render again from that pose and repeat until it moves under a centimetre. The hand-placed pose
is the starting point the matcher needs (from a pose metres off the render shows another place and nothing matches);
the pooled matches then place it finer than key steps can. A result more than MAX_M metres or MAX_DEG degrees from the
hint is taken as a mismatch and not written.
Measured on KNT, it did not beat the hand: the rotation moved 1.6 deg and kept wobbling 0.1-0.5 deg a round, and the
blurred-pixel distance to the DVR on pad frames #20/#50/#80 went 0.564/0.535/0.531 (hand) -> 0.571/0.542/0.539. Freed,
the position crept 32, 14, 9, 6, 4 cm a round towards wherever it was rendered from, while its formal std read 1 mm:
the render's own viewpoint echoes into the answer (the k of cpr_track.py), which that std does not see. Not in the chain.
Writes pad_refined.json next to pad.json: {pos, quat, hint: <pad.json's pos and quat>, ...}; takeoff.py uses it only
while its hint still equals pad.json, so placing the pad again by hand makes a stale refinement drop out.
usage (mastenv, with gsplat): pad_refine.py scene.ply dvr_pinhole.mp4 poses.json(with "takeoff")
env: FRAMES (12), ROUNDS (5), NEAR (1.0), MAX_M (0.2: the hand placement is within 20 cm), MAX_DEG (10), POS (0: position held at the hint; 1 = solved, within MAX_M), SKIP (15: frames at the start left out, the race
feed opens on an OSD stats screen)"""
import json, sys, os, numpy as np, torch, cv2
sys.path.insert(0, os.path.expanduser("~/mast3r"))
from mast3r.model import AsymmetricMASt3R
from mast3r.fast_nn import fast_reciprocal_NNs
from dust3r.inference import inference
from plyfile import PlyData
from gsplat import rasterization
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from frustum import rasterize_culled   # centre-in-view culling, as PlayCanvas
from scipy.spatial.transform import Rotation as Rot
from scipy.optimize import least_squares
dev = "cuda"; E = lambda k, d: float(os.environ.get(k, d))
NEAR, FRAMES, ROUNDS, MAX_M, MAX_DEG, SKIP, SUB = E("NEAR", 1.0), int(E("FRAMES", 12)), int(E("ROUNDS", 5)), E("MAX_M", 0.2), E("MAX_DEG", 10), int(E("SKIP", 15)), 4
POS = bool(int(E("POS", 0)))
ply, video, poses_path = sys.argv[1:4]; D = os.path.dirname(os.path.abspath(video))
hint = json.load(open(f"{D}/pad.json")); take = json.load(open(poses_path))["takeoff"]
# ---- scene and camera (as cpr_track.py)
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
def render(R, c):
    M = np.eye(4); M[:3, :3] = R.as_matrix().T; M[:3, 3] = -M[:3, :3] @ c
    with torch.no_grad():
        o, a, _ = rasterize_culled(means, quats, scales, opac, colors, T(M)[None], Krt[None], RW, RH, sh_degree=1, render_mode="RGB+ED", near_plane=NEAR)
    return (o[0, ..., :3].clamp(0, 1) * 255).byte().cpu().numpy(), o[0, ..., 3].cpu().numpy(), a[0, ..., 0].cpu().numpy(), M
model = AsymmetricMASt3R.from_pretrained("naver/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric").to(dev).eval()
def view(img, idx): return dict(img=torch.tensor(img, dtype=torch.float32).permute(2, 0, 1)[None] / 255 * 2 - 1, true_shape=np.int32([img.shape[:2]]), idx=idx, instance=str(idx))
# ---- the pad frames: evenly over the pad, clear of the opening screen and of the last frames before lift-off
want = set(np.linspace(SKIP, max(SKIP, take - 5), FRAMES).round().astype(int).tolist()); frames = []
cap = cv2.VideoCapture(video); i = 0
while i <= max(want):
    ok, fr = cap.read()
    if not ok: break
    if i in want: frames.append(cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), (RW, RH), interpolation=cv2.INTER_AREA))
    i += 1
R, c = Rot.from_quat(hint["quat"]), np.array(hint["pos"], float); R0, c0 = R, c.copy()
print(f"pad frames {sorted(want)} (takeoff #{take}); hint {np.round(c, 3).tolist()}", flush=True)
for rnd in range(ROUNDS):
    rgb, dep, al, M = render(R, c)
    with torch.no_grad(): o = inference([(view(f, 0), view(rgb, 1)) for f in frames], model, dev, batch_size=len(frames), verbose=False)
    Xs, uvs = [], []
    for k in range(len(frames)):
        a, b = fast_reciprocal_NNs(o["pred1"]["desc"][k], o["pred2"]["desc"][k], subsample_or_initxy1=SUB, device=dev, dist="dot", block_size=2**13)
        a, b = np.asarray(a), np.asarray(b); d = dep[b[:, 1], b[:, 0]]
        keep = qvalid[a[:, 1], a[:, 0]] & (al[b[:, 1], b[:, 0]] > 0.9) & (d > 0.3) & (d < 200)
        a, b, d = a[keep], b[keep], d[keep]
        xc = np.c_[(b[:, 0] - Kr[0, 2]) / Kr[0, 0], (b[:, 1] - Kr[1, 2]) / Kr[1, 1], np.ones(len(b))] * d[:, None]
        Xs.append((np.linalg.inv(M) @ np.c_[xc, np.ones(len(xc))].T).T[:, :3]); uvs.append((a + 0.5) / s - 0.5)
    X, uv = np.concatenate(Xs), np.concatenate(uvs)
    rv0 = cv2.Rodrigues(R.as_matrix().T)[0].reshape(3, 1); tv0 = (-R.as_matrix().T @ c).reshape(3, 1)
    ok, rv, tv, inl = cv2.solvePnPRansac(X, uv, Kfull, None, rv0, tv0, useExtrinsicGuess=True, iterationsCount=3000, reprojectionError=4.0, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok or inl is None or len(inl) < 100: print(f"round {rnd}: PnP failed ({0 if inl is None else len(inl)} inliers)"); sys.exit(1)
    inl = inl[:, 0]; Xi, ui = X[inl], uv[inl]
    # The inliers are pooled from frames that share one pose; solve that pose on them. Most of the points are far, and
    # far points fix the rotation but hardly the position: solved freely, the position crept 32, 14, 9, 6, 4 cm a round
    # the same way (KNT) towards wherever it was rendered from. So by default the position stays at the hint and only
    # the rotation is solved; POS=1 frees the position too, held within MAX_M of the hint.
    def r6(x, Rb, cb):                                   # reprojection residual / 2 px at (rotvec x[:3] * Rb, cb + x[3:])
        d = (Rot.from_rotvec(x[:3]) * Rb).inv().apply(Xi - cb - x[3:])
        return ((np.c_[Kfull[0, 0] * d[:, 0] / d[:, 2] + Kfull[0, 2], Kfull[1, 1] * d[:, 1] / d[:, 2] + Kfull[1, 2]] - ui) / 2.0).ravel()
    sol = least_squares(lambda x: r6(np.r_[x, np.zeros(3)] if not POS else x, R, c), np.zeros(6 if POS else 3), loss="huber", f_scale=1.0)
    Rn = Rot.from_rotvec(sol.x[:3]) * R; cn = c + (sol.x[3:] if POS else 0)
    if POS and np.linalg.norm(cn - c0) > MAX_M: cn = c0 + (cn - c0) * MAX_M / np.linalg.norm(cn - c0)
    # how well these points pin the position at all: translation std with the rotation marginalised (6-dof Gauss-Newton
    # at the solution, 2 px per residual as above; matches from 12 frames of one view are not independent, so this is
    # a lower bound)
    J = least_squares(lambda x: r6(x, Rn, cn), np.zeros(6), max_nfev=1).jac
    Hm = J.T @ J; Ht = Hm[3:, 3:] - Hm[3:, :3] @ np.linalg.solve(Hm[:3, :3], Hm[:3, 3:]); sd = np.sqrt(np.diag(np.linalg.inv(Ht)))
    res = np.linalg.norm(2.0 * r6(np.zeros(6), Rn, cn).reshape(-1, 2), axis=1)
    far = np.linalg.norm(Xi - cn, axis=1) >= 15
    step, turn = np.linalg.norm(cn - c), np.degrees((R.inv() * Rn).magnitude())
    print(f"round {rnd}: {len(inl)}/{len(X)} inliers over {len(frames)} frames ({far.sum()} beyond 15 m), residual p50 {np.median(res):.2f} px"
          f" (far {np.median(res[far]) if far.any() else float('nan'):.2f}); moved {step:.3f} m {turn:.2f} deg; position std x/y/z {np.round(sd, 3).tolist()} m", flush=True)
    R, c = Rn, cn
    if step < 0.01 and turn < 0.05: break
dm, dd = np.linalg.norm(c - c0), np.degrees((R0.inv() * R).magnitude())
print(f"from the hint: {dm:.3f} m, {dd:.2f} deg -> {np.round(c, 3).tolist()}")
if dm > MAX_M or dd > MAX_DEG: print(f"further than {MAX_M} m / {MAX_DEG} deg from the hint: taken as a mismatch, not written"); sys.exit(1)
json.dump({"pos": np.round(c, 4).tolist(), "quat": np.round(R.as_quat(), 7).tolist(), "hint": hint, "inliers": int(len(inl)),
           "residual_px": round(float(np.median(res)), 3), "from_hint_m": round(float(dm), 3), "from_hint_deg": round(float(dd), 3)},
          open(f"{D}/pad_refined.json", "w"), indent=1)
print(f"wrote {D}/pad_refined.json")
