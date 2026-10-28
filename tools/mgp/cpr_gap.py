"""Fill a round's gaps in the final row by rendering the 3DGS and matching (GS-CPR, as tools/dvr/cpr_track.py): where
localize.py found nothing (passing under NEROS and TEAM BLACKSHEEP the view is a close-up of prints and grass, 0-8
inliers), track frame by frame from the solved frames on both sides of the gap: predict the pose (velocity over the
last few), render RGB + depth there, match the frame to the render with MASt3R, lift the render's side of the matches
with its depth, PnP. Frames that localize.py did place but cannot be right are re-solved too: one under the TBS gate
at the upper opening's height between the pass under NEROS and the low gate (the two TBS bars carry the same print),
and one that jumps from both neighbours faster than JUMP_MPS.
usage (rt4090, ~/mastenv): cpr_gap.py <scene.ply in map coords> <top_frame.json> <fit.json> <round loc dir> <round frames
dir: images/<cell>, masks/<cell>> <out dir> [cell ...]
writes <out dir>/<cell>.json: the loc json with the gap frames solved ("how": "render") or left ok: false.
env: MIN_INL (60), MINGAP / MAXGAP (3 / 45 frames: a shorter gap is bridged well enough by the smoothing), NEAR (0.5 m),
PROBE_ROUNDS (2: match again from the pose found), GATE_* (below), DEBUG
Needs ninja and nvcc on PATH the first time (gsplat builds its CUDA part): PATH=~/mastenv/bin:/usr/local/cuda/bin:$PATH"""
import json, os, sys, time
import numpy as np, torch, cv2
from plyfile import PlyData
from scipy.spatial.transform import Rotation as Rot
sys.path.insert(0, os.path.expanduser("~/mast3r")); sys.path.insert(0, os.path.expanduser("~/rt"))
from mast3r.model import AsymmetricMASt3R
from dust3r.inference import inference
from frustum import rasterize_culled
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cpr_gap_lib import reciprocal_nns

dev = "cuda"
ply, frame_json, fit_json, loc_dir, frames_dir, out_dir = sys.argv[1:7]; cells = sys.argv[7:]
os.makedirs(out_dir, exist_ok=True)
MIN_INL, MAXGAP, MINGAP = int(os.environ.get("MIN_INL", 60)), int(os.environ.get("MAXGAP", 45)), int(os.environ.get("MINGAP", 3))
# an answer farther from the prediction than GATE_M + GATE_MPS x (time since the last answer) is another place (on a
# half-trained scene, 400-1000 inliers 5-40 m off); 4 m let a 43 m/s step through on Round 44
# (on the trained scene a gap's first frame often matched 500-1000 points 2.4-3.7 m from a prediction drawn from noisy
# frames: an answer that strong gets STRONG_M more); the angle allows a quad's flip (900 deg/s); SKIP frames that fail
# in a row are passed over, the prediction carried on, before the direction gives up
GATE_M, GATE_MPS, GATE_DEG, GATE_DPS = [float(os.environ.get(k, d)) for k, d in (("GATE_M", 1.0), ("GATE_MPS", 20), ("GATE_DEG", 15), ("GATE_DPS", 900))]
STRONG, STRONG_M, SKIP = int(os.environ.get("STRONG", 400)), float(os.environ.get("STRONG_M", 4.0)), int(os.environ.get("SKIP", 2))
ROUNDS, DEBUG, JUMP_MPS = int(os.environ.get("PROBE_ROUNDS", 2)), os.environ.get("DEBUG") == "1", float(os.environ.get("JUMP_MPS", 45))
F, Tf = json.load(open(frame_json)), json.load(open(fit_json))
E, a0 = np.array([F["ex"], F["ey"], F["up"]]), np.array(F["origin"]); R2 = np.array(Tf["R"]) @ np.diag([1, Tf["mirror"]])
U = float(Tf["scale"])                                   # metres per map unit
def metric(X):                                           # map -> track frame (x = VD x, y = VD z, z = up), metres
    G = (np.atleast_2d(X) - a0) @ E.T * U
    return np.c_[G[:, :2] @ R2.T + Tf["t"], G[:, 2]]
NEAR = float(os.environ.get("NEAR", 0.5)) / U            # splats closer than this are not drawn (map units)
# ---- scene
v = PlyData.read(ply)["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.1]
T = lambda a: torch.tensor(a, dtype=torch.float32, device=dev)
means, quats = T(np.c_[v["x"], v["y"], v["z"]]), T(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]])
scales, opac = torch.exp(T(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]])), torch.sigmoid(T(v["opacity"]))
sh0 = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :]
nrest = len([n for n in v.dtype.names if n.startswith("f_rest_")]) // 3
rest = np.stack([np.c_[[v[f"f_rest_{c * nrest + k}"] for k in range(min(nrest, 3))]].T for c in range(3)], axis=2)
colors = T(np.concatenate([sh0, rest], axis=1)[:, :4])
print(f"{len(v):,} splats", flush=True)
RW, RH = 512, 384
Kp = np.array([[213.0, 0, RW / 2], [0, 213.0, RH / 2], [0, 0, 1]])      # the pinhole the fisheye cell is turned into
Kt = T(Kp)
def w2c(R, c): M = np.eye(4); M[:3, :3] = R.as_matrix().T; M[:3, 3] = -M[:3, :3] @ c; return M
def render(R, c):
    vm = w2c(R, c)[None]
    with torch.no_grad():
        o, a, _ = rasterize_culled(means, quats, scales, opac, colors, T(vm), Kt[None], RW, RH, sh_degree=1, render_mode="RGB+ED", near_plane=NEAR)
    return (o[0, ..., :3].clamp(0, 1) * 255).byte().cpu().numpy(), o[0, ..., 3].cpu().numpy(), a[0, ..., 0].cpu().numpy(), vm[0]
# ---- matching
model = AsymmetricMASt3R.from_pretrained("naver/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric").to(dev).eval()
def view(img, idx): return dict(img=torch.tensor(img, dtype=torch.float32).permute(2, 0, 1)[None] / 255 * 2 - 1, true_shape=np.int32([img.shape[:2]]), idx=idx, instance=str(idx))
def solve(q, valid, R, c):
    """render at (R, c), match the frame q to it, PnP -> (Rot world-from-camera, centre, inliers) or None"""
    rgb, dep, al, vm = render(R, c)
    with torch.no_grad(): o = inference([(view(q, 0), view(rgb, 1))], model, dev, batch_size=1, verbose=False)
    a, b = reciprocal_nns(o["pred1"]["desc"][0], o["pred2"]["desc"][0])
    a, b = np.asarray(a), np.asarray(b); d = dep[b[:, 1], b[:, 0]]
    k = valid[a[:, 1], a[:, 0]] & (al[b[:, 1], b[:, 0]] > 0.9) & (d > NEAR) & (d < 200 / U)
    a, b, d = a[k], b[k], d[k]
    if len(a) < 12: return None
    xc = np.c_[(b[:, 0] - Kp[0, 2]) / Kp[0, 0], (b[:, 1] - Kp[1, 2]) / Kp[1, 1], np.ones(len(b))] * d[:, None]
    X = (np.linalg.inv(vm) @ np.c_[xc, np.ones(len(xc))].T).T[:, :3]; uv = a.astype(np.float64)
    ok, rv, tv, inl = cv2.solvePnPRansac(X, uv, Kp, None, iterationsCount=2000, reprojectionError=4.0, flags=cv2.SOLVEPNP_SQPNP)
    if not ok or inl is None: return None
    inl = inl[:, 0]; rv, tv = cv2.solvePnPRefineLM(X[inl], uv[inl], Kp, None, rv, tv)
    Rw = cv2.Rodrigues(rv)[0]
    return Rot.from_matrix(Rw.T), -Rw.T @ tv[:, 0], int(len(inl))
def solve_iter(q, valid, R, c):
    best = None
    for _ in range(ROUNDS):                          # match again from the pose it found: a closer render matches more
        r = solve(q, valid, R, c)
        if r is None or (best and r[2] <= best[2]): break
        best = r; R, c = r[0], r[1]
    return best
# ---- the row (track frame), as poses_from_loc.py's storey rule
CBOX = (-131, -104, -8, 22)
ALL = os.environ.get("ALL") == "1"                    # every gap on the course (a dusk final tracked from cpr_reloc.py's anchors)
def in_box(m): return ALL or (CBOX[0] < m[0] < CBOX[1] and CBOX[2] < m[1] < CBOX[3])
def suspect(ts, M, ok):
    """indices of solved frames that cannot be right: under the TBS gate at the upper opening's height in the stretch
    from under NEROS to the low gate, or a jump faster than JUMP_MPS from both neighbours"""
    storey, jump = set(), set()
    under = [i for i in range(len(ts)) if ok[i] and -116.5 < M[i, 0] < -110 and 2 < M[i, 1] < 10 and M[i, 2] < 1.6]
    for i in under:
        j = i + 1
        while j < len(ts) and ts[j] - ts[i] <= 4000 and not (ok[j] and M[j, 0] < -126.5 and 5 < M[j, 1] < 9):
            if ok[j] and -126.5 < M[j, 0] < -121 and 4 < M[j, 1] < 10 and M[j, 2] > 2.0: storey.add(j)
            j += 1
    idx = [i for i in range(len(ts)) if ok[i]]
    for p, i, n in zip(idx, idx[1:], idx[2:]):
        sp = lambda a, b: np.linalg.norm(M[a] - M[b]) / max((ts[b] - ts[a]) / 1000, 1e-3)
        if sp(p, i) > JUMP_MPS and sp(i, n) > JUMP_MPS: jump.add(i)
    return storey, jump - storey
def pose_of(f):                                       # loc json frame -> (Rot world-from-camera, centre), map coords
    w, x, y, z = f["qvec"]; return Rot.from_quat([x, y, z, w]).inv(), np.array(f["center"])
def track(order, seed, frames, valid, solved):
    hist = list(seed)                                 # (t_ms, Rot, centre) of the last solved frames, in this direction
    misses = 0
    for i in order:
        if i in solved: break                         # met the other direction
        t = frames[i]["t_ms"]
        if len(hist) >= 2:
            tt = np.array([h[0] for h in hist], float); P = np.array([h[2] for h in hist])
            vel = np.polyfit(tt, P, 1)[0] if len(hist) >= 3 else (P[-1] - P[-2]) / (tt[-1] - tt[-2])
            tb, Rb, pb = hist[-1]
            w = np.mean([(a[1].inv() * b[1]).as_rotvec() / (b[0] - a[0]) for a, b in zip(hist[:-1], hist[1:])], axis=0)
            pred = (Rb * Rot.from_rotvec(w * (t - tb)), pb + vel * (t - tb))
        else:
            pred = (hist[-1][1], hist[-1][2])
        q, m = load(frames[i]["t_ms"])
        r = solve_iter(q, m, *pred)
        dt = abs(t - hist[-1][0]) / 1000
        if r is not None:
            dm = np.linalg.norm(r[1] - pred[1]) * U; dd = np.degrees((r[0].inv() * pred[0]).magnitude())
        strong = r is not None and r[2] >= STRONG     # a strong answer may also turn twice as fast (Round 44: 682 points, 75 deg)
        if r is None or r[2] < MIN_INL or dm > GATE_M + GATE_MPS * dt + (STRONG_M if strong else 0) or dd > GATE_DEG + GATE_DPS * dt * (2 if strong else 1):
            misses += 1
            if DEBUG: print(f"    {t / 1000:.3f}: miss {misses} ({'none' if r is None else f'{r[2]} inl, {dm:.2f} m, {dd:.0f} deg'})", flush=True)
            if misses > SKIP: break
            continue
        misses = 0
        solved[i] = r; hist = (hist + [(t, r[0], r[1])])[-4:]
        if DEBUG: print(f"    {t / 1000:.3f}: {r[2]} inliers, {metric(r[1])[0].round(2)}", flush=True)
for fn in sorted(os.listdir(loc_dir)):
    cell = fn[:-5]
    if not fn.endswith(".json") or (cells and cell not in cells):
        continue
    J = json.load(open(f"{loc_dir}/{fn}")); frames = J["frames"]
    if not os.path.isdir(f"{frames_dir}/images/{cell}"):   # never near the row: no frames were sent, nothing to fill
        json.dump(J, open(f"{out_dir}/{fn}", "w")); print(f"{cell}: no frames here, as it was", flush=True); continue
    cam = J["camera"]; fx, fy, cx, cy, *k = cam["params"]
    Kf = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])
    m1, m2 = cv2.fisheye.initUndistortRectifyMap(Kf, np.array(k[:4]), np.eye(3), Kp, (RW, RH), cv2.CV_16SC2)
    def load(t_ms):
        img = cv2.imread(f"{frames_dir}/images/{cell}/{t_ms:07d}.jpg")
        mk = cv2.imread(f"{frames_dir}/masks/{cell}/{t_ms:07d}.jpg.png", cv2.IMREAD_GRAYSCALE)
        if mk is None: mk = np.full(img.shape[:2], 255, np.uint8)
        q = cv2.cvtColor(cv2.remap(img, m1, m2, cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
        valid = cv2.erode(cv2.remap(mk, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0), np.ones((9, 9), np.uint8)) > 0
        return q, valid
    ts = [f["t_ms"] for f in frames]; ok = [bool(f["ok"]) for f in frames]
    M = np.array([metric(np.array(f["center"]))[0] if f["ok"] else [np.nan] * 3 for f in frames])
    storey, jump = suspect(ts, M, ok)
    # only where the gaps get solved: elsewhere an unsolved "jump" was simply dropped, and on the straight before the
    # start gate (44 m/s) good frames went that way and took a lap with them (Round 61 MINJAE_: 20.4 s off)
    jump = {i for i in jump if in_box(M[i])}
    for i in storey | jump: ok[i] = False
    on_disk = set(int(f[:-4]) for f in os.listdir(f"{frames_dir}/images/{cell}") if f.endswith(".jpg") and f[:-4].isdigit())
    solved, t0, gaps = {}, time.time(), 0
    i = 0
    while i < len(frames):
        if ok[i]: i += 1; continue
        a = i - 1; b = i
        while b < len(frames) and not ok[b]: b += 1
        # a gap a+1 .. b-1 between solved frames a and b, in the row, short, frames on disk
        if a >= 1 and b < len(frames) and MINGAP <= b - a - 1 <= MAXGAP and in_box(M[a]) and in_box(M[b]) and all(ts[j] in on_disk for j in range(a + 1, b)):
            gaps += 1
            if DEBUG: print(f"  gap {ts[a + 1] / 1000:.3f}-{ts[b - 1] / 1000:.3f} ({b - a - 1} frames)", flush=True)
            seed_f = [(ts[j], *pose_of(frames[j])) for j in range(max(0, a - 3), a + 1) if ok[j]]
            seed_b = [(ts[j], *pose_of(frames[j])) for j in range(min(len(frames) - 1, b + 3), b - 1, -1) if ok[j]]
            if seed_f: track(range(a + 1, b), seed_f, frames, None, solved)
            if seed_b: track(range(b - 1, a, -1), seed_b, frames, None, solved)
        i = b
    # unsolved: a jump goes; a frame one storey up stays as it was, for poses_from_loc.py's storey rule to bring down
    for j in jump:
        if j not in solved: frames[j] = {"t_ms": ts[j], "ok": False, "inliers": 0, "how": "jump"}
    for j, (R, c, n) in solved.items():
        q = R.inv().as_quat()                         # cam_from_world, x y z w -> w x y z as localize.py writes
        frames[j] = {"t_ms": ts[j], "ok": True, "inliers": n, "center": np.round(c, 5).tolist(),
                     "qvec": [float(q[3]), float(q[0]), float(q[1]), float(q[2])], "how": "render"}
    J["cpr_gap"] = {"gaps": gaps, "solved": len(solved), "storey": len(storey), "jump": len(jump)}
    json.dump(J, open(f"{out_dir}/{fn}", "w"))
    print(f"{cell}: {gaps} gaps, {len(solved)} frames solved by rendering; suspect: {len(storey)} one storey up, "
          f"{len(jump)} jumps ({time.time() - t0:.0f} s)", flush=True)
