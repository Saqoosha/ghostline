"""Place frames that the map's features cannot (dusk and night Mains: 0-15% against the daytime map) by rendering the
3DGS: the scan rendered small at reference poses along the course (the map's own cameras, every 4th), a DINOv2 global
descriptor for each; a frame's descriptor picks the TOPK nearest renders, each is rendered full size and matched with
MASt3R, lifted with its depth, PnP; the answer with the most inliers wins (a second round from the pose it found).
Anchors for cpr_gap.py to track from.
usage (rt4090, ~/mastenv): cpr_reloc.py <scene.ply> <top_frame.json> <fit.json> <refs.json> <round loc dir> <round frames dir>
<out dir> [cell ...]
  refs.json: [{"name", "R_wc" (world-from-camera 3x3), "c"}, ...] in map coords (dumped from the map model)
writes <out dir>/<cell>.json: the loc json with the frames placed here ("how": "reloc"), every EVERY-th frame tried.
env: EVERY (5), TOPK (8), MIN_INL (80), DEBUG"""
import json, os, sys, time
import numpy as np, torch, cv2
from scipy.spatial.transform import Rotation as Rot
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cpr_gap_lib import Scene, Matcher, fisheye_to_pinhole

ply, frame_json, fit_json, refs_json, loc_dir, frames_dir, out_dir = sys.argv[1:8]; cells = sys.argv[8:]
os.makedirs(out_dir, exist_ok=True)
EVERY, TOPK, MIN_INL, DEBUG = int(os.environ.get("EVERY", 5)), int(os.environ.get("TOPK", 8)), int(os.environ.get("MIN_INL", 80)), os.environ.get("DEBUG") == "1"
# where a quad can be (track frame, m: x0, x1, y0, y1, z0, z1): on dusk frames, 120-890 inliers also came 22 m up or under
# the ground (B final)
BOUNDS = [float(v) for v in os.environ.get("BOUNDS", "-140,-70,-15,85,-1,12").split(",")]
dev = "cuda"
scene = Scene(ply, frame_json, fit_json)
match = Matcher(scene)
# ---- reference renders and their DINOv2 descriptors
dino = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14").to(dev).eval()
MEAN = torch.tensor([0.485, 0.456, 0.406], device=dev)[:, None, None]; STD = torch.tensor([0.229, 0.224, 0.225], device=dev)[:, None, None]
def gdesc(imgs):
    x = torch.stack([(torch.tensor(cv2.resize(im, (224, 168)), dtype=torch.float32, device=dev).permute(2, 0, 1) / 255 - MEAN) / STD for im in imgs])
    with torch.no_grad(): f = dino(x)
    return torch.nn.functional.normalize(f, dim=1)
refs = json.load(open(refs_json))
ref_poses = [(Rot.from_matrix(np.array(r["R_wc"])), np.array(r["c"])) for r in refs]
t0 = time.time(); descs = []
for k in range(0, len(ref_poses), 32):
    descs.append(gdesc([scene.render(R, c, small=True)[0] for R, c in ref_poses[k:k + 32]]))
ref_desc = torch.cat(descs)
print(f"{len(ref_poses)} reference renders described ({time.time() - t0:.0f} s)", flush=True)
for fn in sorted(os.listdir(loc_dir)):
    cell = fn[:-5]
    if not fn.endswith(".json") or (cells and cell not in cells):
        continue
    J = json.load(open(f"{loc_dir}/{fn}")); frames = J["frames"]
    if not os.path.isdir(f"{frames_dir}/images/{cell}"):
        continue
    load = fisheye_to_pinhole(J["camera"], f"{frames_dir}/images/{cell}", f"{frames_dir}/masks/{cell}")
    t1, placed = time.time(), 0
    for i in range(0, len(frames), EVERY):
        f = frames[i]
        if f.get("ok"):
            continue
        q, valid = load(f["t_ms"])
        top = torch.topk(ref_desc @ gdesc([q])[0], TOPK).indices.tolist()
        best = None
        for k in top:
            r = match.solve_iter(q, valid, *ref_poses[k])
            if r is not None and (best is None or r[2] > best[2]): best = r
        if best is None or best[2] < MIN_INL:
            if DEBUG: print(f"  {f['t_ms'] / 1000:.3f}: {'none' if best is None else best[2]}", flush=True)
            continue
        m = scene.metric(best[1])[0]
        if not (BOUNDS[0] < m[0] < BOUNDS[1] and BOUNDS[2] < m[1] < BOUNDS[3] and BOUNDS[4] < m[2] < BOUNDS[5]):
            if DEBUG: print(f"  {f['t_ms'] / 1000:.3f}: {best[2]} inliers but off the course at {m.round(1)}", flush=True)
            continue
        R, c, n = best; qv = R.inv().as_quat()
        frames[i] = {"t_ms": f["t_ms"], "ok": True, "inliers": n, "center": np.round(c, 5).tolist(),
                     "qvec": [float(qv[3]), float(qv[0]), float(qv[1]), float(qv[2])], "how": "reloc"}
        placed += 1
        if DEBUG: print(f"  {f['t_ms'] / 1000:.3f}: {n} inliers at {scene.metric(c)[0].round(2)}", flush=True)
    json.dump(J, open(f"{out_dir}/{fn}", "w"))
    print(f"{cell}: {placed} frames placed of {len(range(0, len(frames), EVERY))} tried ({time.time() - t1:.0f} s)", flush=True)
