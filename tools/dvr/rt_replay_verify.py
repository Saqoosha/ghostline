"""Check answers against the scan without ground truth: render the scan at each answer's pose, match the cell's own picture to
that render (XFeat + LighterGlue, as the tracker), and count the matches the pose itself explains (reprojection under 4 px).
A right pose explains many; one a metre off explains few. Two replays of one recording are sampled the same way, split into
frames both solved and frames only one solved (rt_replay_accuracy.py's split), so the gained answers can be judged.
  rt_replay_verify.py <recording.mp4> <map.npz> <scene.ply> <dir A> <dir B> [cell ...]     (dirs as rt_replay_day.sh writes)
env: N (150) frames sampled per cell and split, DIS (0 m: off) the split of answers that disagree, SHIFT (-20:400) frames tried, GLUE / XFEAT (the tracker's TensorRT engines)"""
import glob, json, os, sys, numpy as np, torch, cv2
from scipy.spatial.transform import Rotation as Rot
from rt_trt import Trt
from rt_render import Scene
rec, MAP, PLY, A, B = sys.argv[1:6]; CELLS = sys.argv[6:] or ["R2", "F1", "R4", "R5"]
RECT = {"R2": (0, 0), "F1": (960, 0), "R4": (0, 540), "R5": (960, 540)}; N = int(os.environ.get("N", 150)); TOPK = 2048
CAMS = ["cams/hdzero-nano90-upgrade-lens.json", "cams/hdzero-nano90-stock-lens.json"]   # rt_replay_day.sh's order: "lens" indexes it
m = np.load(MAP); K = m["K"]; RW, RH = [int(v) for v in m["size"]]; dev = "cuda"
xf, glue = Trt(os.environ["XFEAT"]), Trt(os.environ["GLUE"])
scene = Scene(PLY, RW, RH, 1.0, TOPK, xf)
def lens_maps(cj):                                   # as rt_track.py's Stream: the cell pixel for each pinhole pixel, and the valid mask
    c = json.load(open(cj)); f = c["source_fisheye"]; W, H = c["width"], c["height"]
    Kf = np.array([[f["fx"], 0, f["cx"]], [0, f["fy"], f["cy"]], [0, 0, 1]]); Kfull = np.array([[c["fx"], 0, c["cx"]], [0, c["fy"], c["cy"]], [0, 0, 1]])
    mx, my = cv2.fisheye.initUndistortRectifyMap(Kf, np.array(f["k"]), np.eye(3), K, (RW, RH), cv2.CV_32FC1)
    a1, a2 = cv2.fisheye.initUndistortRectifyMap(Kf, np.array(f["k"]), np.eye(3), Kfull, (W, H), cv2.CV_16SC2)
    o = np.full((H, W), 255, np.uint8); o[:82] = 0; o[632:] = 0
    qv = cv2.resize((cv2.erode(cv2.remap(o, a1, a2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0), np.ones((9, 9), np.uint8)) > 0).astype(np.uint8), (RW, RH), interpolation=cv2.INTER_NEAREST) > 0
    return mx * 960 / W, my * 540 / H, torch.tensor(qv, device=dev)
LENS = [lens_maps(c) for c in CAMS]
def load(d, cell):
    f = glob.glob(f"{d}/{os.path.basename(rec)[:-4]}/{cell}.jsonl")
    return {r["i"]: r for r in map(json.loads, open(f[0])) if "pos" in r} if f else {}
def cellimg(fr, cell, r):                             # the cell's picture through the answer's lens, as the tracker saw it
    x0, y0 = RECT[cell]; mx, my, qv = LENS[r.get("lens", 0)]
    img = cv2.cvtColor(cv2.remap(fr, mx + x0, my + y0, cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
    kp0, d0, sc = xf(torch.from_numpy(img).to(dev).permute(2, 0, 1)[None].float() / 255); ix = kp0.long()
    return kp0, d0, (sc > 0) & qv[ix[..., 1], ix[..., 0]]
def check(fr, cell, r, v=None):                      # -> matches, matches the pose explains (< 4 px), their median reprojection error
    kp0, d0, ok = cellimg(fr, cell, r); R, c = Rot.from_quat(r["quat"]), np.array(r["pos"]); v = v or scene.views([(R, c)], [K])[0]
    m0 = glue(kp0 * ok[..., None], d0 * ok[..., None], v["kp"][None], v["desc"][None].float())[0][0].cpu().numpy()
    q = np.nonzero((m0 >= 0) & ok[0].cpu().numpy())[0]; j = m0[q]; keep = j < v["n"]; q, j = q[keep], j[keep]
    uv, X = kp0[0].cpu().numpy()[q], v["X"][j]; P = (X - c) @ R.as_matrix(); front = P[:, 2] > 0.1
    err = np.linalg.norm((P[:, :2] / np.maximum(P[:, 2:], 1e-6)) * [K[0, 0], K[1, 1]] + [K[0, 2], K[1, 2]] - uv, axis=1)[front]
    return len(q), int((err < 4).sum()), float(np.median(err)) if len(err) else 99.0
def frames_at(want):                                 # {recording frame: picture} for the wanted frames, one pass over the video
    out, cap, n, it = {}, cv2.VideoCapture(rec), 0, iter(sorted(want)); w = next(it, None)
    while w is not None:
        ok, fr = cap.read()
        if not ok: break
        if n == w: out[n] = fr; w = next(it, None)
        n += 1
    return out
# rt_track.py's frame i counts from the first frame its decoder got over SRT, which is not the recording's first (161601: ~118
# frames late, the start of the stream lost). Per run: the shift that lets its strongest answers explain the most matches.
SHIFT = range(*[int(v) for v in os.environ.get("SHIFT", "-20:400").split(":")])   # the shifts tried (161601 and the late heats: 120)
def offset(d):
    rows = sorted((r | {"cell": c} for c in CELLS for r in load(d, c).values()), key=lambda r: -r["inl"])
    probes = []
    for r in rows:                                   # the strongest answers, at least 20 s apart
        if all(abs(r["i"] - p["i"]) > 1200 for p in probes): probes.append(r)
        if len(probes) == 4: break
    score = np.zeros(len(SHIFT)); fr = frames_at({p["i"] + k for p in probes for k in SHIFT if p["i"] + k >= 0})
    for p in probes:
        v = scene.views([(Rot.from_quat(p["quat"]), np.array(p["pos"]))], [K])[0]
        for a, k in enumerate(SHIFT):
            if p["i"] + k in fr: score[a] += check(fr[p["i"] + k], p["cell"], p, v)[1]
    return SHIFT[int(score.argmax())]
OFF = {d: offset(d) for d in (A, B)}; print("frame shift (recording frame - tracker frame):", OFF, flush=True)
def load_at(d, cell): return {i + OFF[d]: r for i, r in load(d, cell).items()}
DIS = float(os.environ.get("DIS", 0))   # also: frames both solved whose answers are over DIS m apart (which one is right?)
rng = np.random.default_rng(0); jobs = []           # (cell, frame, run name, answer row, split)
for cell in CELLS:
    a, b = load_at(A, cell), load_at(B, cell)
    both = sorted(set(a) & set(b)); far = [i for i in both if np.linalg.norm(np.subtract(a[i]["pos"], b[i]["pos"])) > DIS]
    for split, keys, runs in (("both", both, (("A", a), ("B", b))), ("only A", sorted(set(a) - set(b)), (("A", a),)), ("only B", sorted(set(b) - set(a)), (("B", b),)),
                              *([(f"> {DIS:g} m", far, (("A", a), ("B", b)))] if DIS else [])):
        for i in (rng.choice(keys, min(N, len(keys)), replace=False) if keys else []):
            for name, run in runs: jobs.append((cell, int(i), name, run[int(i)], split))
frames = frames_at({i for _, i, _, _, _ in jobs}); print(f"{len(jobs)} answers on {len(frames)} frames", flush=True)
out = []
for cell, i, name, r, split in jobs:                 # i: the recording's frame
    if i not in frames: continue
    mt, ex, md = check(frames[i], cell, r)
    out.append(dict(cell=cell, i=i, run=name, split=split, matches=mt, explained=ex, med=md, inl=r["inl"], lens=r.get("lens", 0)))
json.dump(dict(shift=OFF, answers=out), open(f"verify-{os.path.basename(rec)[9:15]}.json", "w"))
print(f"{'split':8} {'run':3} {'n':>5} {'explained p50':>14} {'p10':>5} {'<30':>6} {'reproj p50 px':>14}")
for split in dict.fromkeys(o["split"] for o in out):
    for name in ("A", "B"):
        e = [o for o in out if o["split"] == split and o["run"] == name]
        if not e: continue
        ex = np.array([o["explained"] for o in e]); md = np.array([o["med"] for o in e])
        print(f"{split:8} {name:3} {len(e):5d} {np.median(ex):14.0f} {np.percentile(ex, 10):5.0f} {np.mean(ex < 30) * 100:5.1f}% {np.median(md):14.1f}")
