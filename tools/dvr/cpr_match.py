"""GS-CPR step 2 (mastenv): match each DVR frame against its candidate renders with MASt3R, lift the render
side to 3D with the rendered depth, and solve PnP-RANSAC in the full-res pinhole camera. The candidate with
the most inliers wins. Writes one JSON line per rendered frame: {i, quat (c2w xyzw), pos, inliers, cand, n_match (after
the alpha/depth/OSD filter), near (share of inliers within 15 m, which sets cpr_fuse.py's position sigma)}, or
{i, inliers: 0} when no candidate solves.
usage: cpr_match.py dvr_pinhole.mp4 render_dir frames.txt out.jsonl"""
import json, sys, os, time, numpy as np, torch, cv2
sys.path.insert(0, os.path.expanduser("~/mast3r"))
from mast3r.model import AsymmetricMASt3R
from mast3r.fast_nn import fast_reciprocal_NNs
from dust3r.inference import inference
from scipy.spatial.transform import Rotation as Rot
dev = "cuda"
video, rdir, frames_txt, out_path = sys.argv[1:5]
MIN_ALPHA, MAX_DEPTH = 0.9, 200.0
DUMP = os.environ.get("DUMP")                       # dir: save each frame's winning inlier correspondences
model = AsymmetricMASt3R.from_pretrained("naver/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric").to(dev).eval()
cam = json.load(open(video + ".json")); W, H = cam["width"], cam["height"]
Kfull = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
fk = cam["source_fisheye"]; Kf = np.array([[fk["fx"], 0, fk["cx"]], [0, fk["fy"], fk["cy"]], [0, 0, 1]])
m1, m2 = cv2.fisheye.initUndistortRectifyMap(Kf, np.array(fk["k"]), np.eye(3), Kfull, (W, H), cv2.CV_16SC2)
osd = np.full((H, W), 255, np.uint8); osd[:82] = 0; osd[632:] = 0
valid = cv2.erode(cv2.remap(osd, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0), np.ones((9, 9), np.uint8)) > 0
def view(img_u8, idx):                               # the dict dust3r's load_images builds
    t = torch.tensor(img_u8, dtype=torch.float32).permute(2, 0, 1)[None] / 255 * 2 - 1
    return dict(img=t, true_shape=np.int32([img_u8.shape[:2]]), idx=idx, instance=str(idx))
frames = [int(x) for x in open(frames_txt).read().split()]
done = set()
if os.path.exists(out_path):
    for l in open(out_path): done.add(json.loads(l)["i"])
out = open(out_path, "a"); cap = cv2.VideoCapture(video); at = 0; t0 = time.time()
for n, i in enumerate(frames):
    if i in done: continue
    while not os.path.exists(f"{rdir}/{i:05d}.npz") and not os.path.exists(f"{rdir}/DONE"): time.sleep(0.5)   # the renderer runs alongside
    if not os.path.exists(f"{rdir}/{i:05d}.npz"): continue
    if i < at: cap.set(cv2.CAP_PROP_POS_FRAMES, i); at = i
    while at < i: cap.grab(); at += 1
    ok, fr = cap.read(); at += 1
    if not ok: break
    R = np.load(f"{rdir}/{i:05d}.npz"); RH, RW = R["rgb"].shape[1:3]; s = W / RW
    q = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), (RW, RH), interpolation=cv2.INTER_AREA)
    qvalid = cv2.resize(valid.astype(np.uint8), (RW, RH), interpolation=cv2.INTER_NEAREST) > 0
    best = None; nc = len(R["rgb"])
    with torch.no_grad():                            # every candidate pair in one forward pass
        o = inference([(view(q, 0), view(R["rgb"][c], 1)) for c in range(nc)], model, dev, batch_size=nc, verbose=False)
    for c in range(nc):
        d1, d2 = o["pred1"]["desc"][c].detach(), o["pred2"]["desc"][c].detach()
        a, b = fast_reciprocal_NNs(d1, d2, subsample_or_initxy1=8, device=dev, dist="dot", block_size=2**13)
        a, b = np.asarray(a), np.asarray(b)                          # (N,2) x,y in query / render
        dep = R["depth"][c][b[:, 1], b[:, 0]]; al = R["alpha"][c][b[:, 1], b[:, 0]].astype(np.float32)
        keep = qvalid[a[:, 1], a[:, 0]] & (al > MIN_ALPHA) & (dep > 0.3) & (dep < MAX_DEPTH)
        a, b, dep = a[keep], b[keep], dep[keep]
        if len(a) < 12: continue
        Kr = R["K"]; xc = np.c_[(b[:, 0] - Kr[0, 2]) / Kr[0, 0], (b[:, 1] - Kr[1, 2]) / Kr[1, 1], np.ones(len(b))] * dep[:, None]
        w2c = R["w2c"][c]; Xw = (np.linalg.inv(w2c) @ np.c_[xc, np.ones(len(xc))].T).T[:, :3]
        uv = (a.astype(np.float64) + 0.5) * s - 0.5
        ok, rv, tv, inl = cv2.solvePnPRansac(Xw, uv, Kfull, None, iterationsCount=2000, reprojectionError=4.0, flags=cv2.SOLVEPNP_SQPNP)
        if not ok or inl is None: continue
        inl = inl[:, 0]
        rv, tv = cv2.solvePnPRefineLM(Xw[inl], uv[inl], Kfull, None, rv, tv)
        if best is None or len(inl) > best["inliers"]:
            Rw = cv2.Rodrigues(rv)[0]
            if DUMP: os.makedirs(DUMP, exist_ok=True); np.savez(f"{DUMP}/{i:05d}.npz", X=Xw[inl], uv=uv[inl])
            C = -Rw.T @ tv[:, 0]; near = float(np.mean(np.linalg.norm(Xw[inl] - C, axis=1) < 15))   # position needs near points
            best = dict(i=i, quat=Rot.from_matrix(Rw.T).as_quat().tolist(), pos=C.tolist(), inliers=int(len(inl)), cand=c, n_match=int(len(a)), near=round(near, 3))
    if best is None: best = dict(i=i, inliers=0)
    out.write(json.dumps(best) + "\n"); out.flush()
    if n % 100 == 0: print(f"{n}/{len(frames)} #{i} inliers {best['inliers']} cand {best.get('cand')} {(time.time()-t0)/(n+1):.2f} s/frame", flush=True)
print("MATCH-DONE", flush=True)
