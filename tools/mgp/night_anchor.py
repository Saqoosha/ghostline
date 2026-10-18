"""Put a learned-feature SfM model (night_sfm_hloc.py, frames added by night_localize.py) on the day map. Anchors are the
frames that both the model and the day map's localizer (localize.py, SIFT: dusk feeds still place some frames there)
placed: a similarity (scale, rotation, shift) is fitted to their camera centres by RANSAC (3-point samples, INL_M m
in the day map), refined on the inliers, and checked on the cameras' rotations. Then every frame of the given feeds is
written as a loc json in day-map coordinates, the model's pose where it has one ("how": "night"); the day localizer's
own poses are not kept (on these feeds most of them are wrong: 28 inliers p50, some cells' focal lengths diverged). The
feed's day loc json is the template (camera, frame list). With FRAME / FIT (to_metric's top_frame.json, fit01.png.json)
frames the model put off the course (height outside MIN_UP..MAX_UP: -1..15 m; with COURSE, a glob of the qualifiers'
pose files, also over MAX_OFF (6) m sideways from their paths: the finished evening map flung 4.6% of its cameras far
off) are dropped.
usage: night_anchor.py <results.txt> <inliers.json> <out loc dir> <day loc dir> ... -- <feed> ...
  day loc dirs hold <feed>.json (frames: t_ms, ok, center, qvec) for anchors and templates; results.txt names are
  <feed>/<t_ms>.jpg
env: INL_M (0.6), M_PER_UNIT (7.367: the day map), MIN_INL (30), FRAME, FIT, MIN_UP, MAX_UP, COURSE, MAX_OFF,
  ANCHOR_PREFIX (e.g. fd_), LOCAL=1 (then a similarity per anchor neighbourhood on top: night_warp.py; LOCAL_K 30,
  LOCAL_M 0.8 m, MIN_DAY_INL 40; the field goes to <out dir>/field.npz), FOLD (with LOCAL: see below)"""
import json, os, sys
import numpy as np
from scipy.spatial.transform import Rotation as Rot

sep = sys.argv.index("--")
res_txt, inl_json, out_dir = sys.argv[1:4]; day_dirs = sys.argv[4:sep]; feeds = sys.argv[sep + 1:]
INL_M, M, MIN_INL = float(os.environ.get("INL_M", 0.6)), float(os.environ.get("M_PER_UNIT", 7.367)), int(os.environ.get("MIN_INL", 30))
inl = json.load(open(inl_json))
night = {}                                                    # name -> (R_cw, centre) in model coordinates
for line in open(res_txt):
    n, *v = line.split(); v = [float(x) for x in v]
    R = Rot.from_quat([v[1], v[2], v[3], v[0]])
    night[n] = (R, -R.inv().apply(v[4:7]))
day = {}                                                      # name -> (R_cw, centre) in day-map coordinates
templates = {}
for d in day_dirs:
    for fn in os.listdir(d):
        if not fn.endswith(".json"):
            continue
        feed = fn[:-5]; J = json.load(open(f"{d}/{fn}")); templates[feed] = J
        for f in J["frames"]:
            if f.get("ok"):
                w, x, y, z = f["qvec"]
                day[f"{feed}/{f['t_ms']:07d}.jpg"] = (Rot.from_quat([x, y, z, w]), np.array(f["center"]), f.get("inliers", 0))
both = sorted(set(night) & set(day))
if os.environ.get("ANCHOR_PREFIX"):                          # only trusted feeds as anchors (the D final, at noon)
    both = [n for n in both if n.startswith(tuple(os.environ["ANCHOR_PREFIX"].split(",")))]
print(f"{len(night)} frames in the model, {len(day)} placed on the day map, {len(both)} anchors "
      f"({', '.join(f'{k}: {v}' for k, v in sorted({n.split('/')[0]: sum(1 for b in both if b.startswith(n.split('/')[0] + '/')) for n in both}.items()))})", flush=True)
A = np.array([night[n][1] for n in both]); B = np.array([day[n][1] for n in both])


def umeyama(A, B):
    ma, mb = A.mean(0), B.mean(0); a, b = A - ma, B - mb
    U, S, Vt = np.linalg.svd(b.T @ a / len(A))
    D = np.eye(3); D[2, 2] = np.sign(np.linalg.det(U @ Vt))
    R = U @ D @ Vt; sc = (S * np.diag(D)).sum() / (a ** 2).sum(1).mean()
    return sc, R, mb - sc * R @ ma


rng = np.random.default_rng(0); thr = INL_M / M; best = None
for _ in range(5000):
    k = rng.choice(len(both), 3, replace=False)
    try:
        s, R, t = umeyama(A[k], B[k])
    except np.linalg.LinAlgError:
        continue
    e = np.linalg.norm(s * A @ R.T + t - B, axis=1)
    n_in = int((e < thr).sum())
    if best is None or n_in > best[0]: best = (n_in, s, R, t)
n_in, s, R, t = best
for _ in range(5):
    keep = np.linalg.norm(s * A @ R.T + t - B, axis=1) < thr
    s, R, t = umeyama(A[keep], B[keep])
e = np.linalg.norm(s * A @ R.T + t - B, axis=1) * M; keep = e < INL_M
ang = [np.degrees((day[n][0] * Rot.from_matrix(R) * night[n][0].inv()).magnitude()) for n, k in zip(both, keep) if k]
print(f"similarity: {keep.sum()} of {len(both)} anchors within {INL_M} m, median {np.median(e[keep]):.2f} m, "
      f"p90 {np.percentile(e[keep], 90):.2f} m; rotations differ by median {np.median(ang):.1f} deg; "
      f"scale {s * M:.4f} m per model unit", flush=True)
for f in sorted({n.split('/')[0] for n in both}):
    m = [i for i, n in enumerate(both) if n.startswith(f + "/")]
    print(f"  {f}: {int(keep[m].sum())} / {len(m)} anchors fit, median {np.median(e[m]):.2f} m", flush=True)
os.makedirs(out_dir, exist_ok=True)
json.dump({"note": "model -> day map: day = A @ model + b", "A": (s * R).tolist(), "b": t.tolist(), "anchors": int(keep.sum()),
           "median_m": float(np.median(e[keep]))}, open(f"{out_dir}/transform.json", "w"), indent=1)
Rm = Rot.from_matrix(R)
field = None
if os.environ.get("LOCAL") == "1":                       # a similarity per anchor neighbourhood (night_warp.py)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import night_warp
    MIN_DAY = int(os.environ.get("MIN_DAY_INL", 40))
    good = [i for i, n in enumerate(both) if day[n][2] >= MIN_DAY]
    P, Q = s * A[good] @ R.T + t, B[good]
    f = night_warp.fit(P, Q, K=int(os.environ.get("LOCAL_K", 30)), thr=float(os.environ.get("LOCAL_M", 0.8)) / M)
    np.savez(f"{out_dir}/field.npz", **f)
    field = night_warp.Field(f)
    loo = np.linalg.norm(field.apply(P, skip_self=True)[0] - Q, axis=1) * M      # each anchor from its neighbours
    print(f"local field: {len(f['P'])} of {len(good)} anchors (day inliers >= {MIN_DAY}) kept; an anchor predicted from "
          f"its neighbours: p50 {np.median(loo):.2f} m, p75 {np.percentile(loo, 75):.2f}, p90 {np.percentile(loo, 90):.2f}", flush=True)
height = course = ground = None
if os.environ.get("FRAME"):
    Fr, Tf = json.load(open(os.environ["FRAME"])), json.load(open(os.environ["FIT"]))
    E, a0, U = np.array([Fr["ex"], Fr["ey"], Fr["up"]]), np.array(Fr["origin"]), float(Tf["scale"])
    R2 = np.array(Tf["R"]) @ np.diag([1, Tf["mirror"]])
    height = lambda X: ((X - a0) @ E.T * U)[2]
    ground = lambda X: ((X - a0) @ E.T * U)[:2] @ R2.T + Tf["t"]      # track frame: x = VD x, y = VD z
    if os.environ.get("COURSE"):                         # the qualifiers' pose files (page world: x, up, -VD z)
        import glob
        from scipy.spatial import cKDTree
        Wq = np.concatenate([np.array([f["center"] for f in json.load(open(p_))["frames"]]) for p_ in glob.glob(os.environ["COURSE"])])
        course = cKDTree(np.c_[Wq[:, 0], -Wq[:, 2]])
lo, hi = float(os.environ.get("MIN_UP", -1)), float(os.environ.get("MAX_UP", 15)); MAX_OFF = float(os.environ.get("MAX_OFF", 6))
# FOLD=x0,x1,z0,z1 (page world m): a part of the course the map folded onto another; frames there are unfolded by
# continuity (night_warp.unfold). The evening map put the left hook (x -131..-127) onto the right arc: -138,-104,-60,-22
FOLD = [float(v) for v in os.environ["FOLD"].split(",")] if os.environ.get("FOLD") and field is not None else None
if FOLD is not None and height is None:
    sys.exit("FOLD needs FRAME / FIT")
for feed in feeds:
    if feed not in templates:
        print(f"{feed}: no day loc json to take the frame list from, skipped"); continue
    J = json.loads(json.dumps(templates[feed])); n_n = n_off = 0
    pose = {}                                                # frame index -> (day-map centre, R_cw)
    idx = [i for i, f in enumerate(J["frames"]) if f"{feed}/{f['t_ms']:07d}.jpg" in night and inl.get(f"{feed}/{f['t_ms']:07d}.jpg", 0) >= MIN_INL]
    if idx:
        cs = [night[f"{feed}/{J['frames'][i]['t_ms']:07d}.jpg"] for i in idx]
        Xg = np.array([s * R @ c + t for _, c in cs]); Rws = [Rc * Rm.inv() for Rc, _ in cs]     # R_cw(day) = R_cw(model) R^T
        if field is None:
            for i, x, rw in zip(idx, Xg, Rws): pose[i] = (x, rw)
        else:
            Y, j, _ = field.apply(Xg); Rl = field.f["R"][j]
            if FOLD is not None and ground is not None:
                gw = np.array([ground(y) for y in Y])          # track frame (x, VD z); FOLD is x0, x1, z0, z1 in page world
                inbox = (gw[:, 0] > FOLD[0]) & (gw[:, 0] < FOLD[1]) & (-gw[:, 1] > FOLD[2]) & (-gw[:, 1] < FOLD[3])
                t_ms = np.array([J["frames"][i]["t_ms"] for i in idx])
                picked, n_alt = night_warp.unfold(t_ms, Xg, Y, Rl, inbox, P, Q, M, K=int(os.environ.get("FOLD_K", 40)), min_n=int(os.environ.get("FOLD_MIN", 6)))
                Y = np.array([p_[0] for p_ in picked]); Rl = np.array([p_[1] for p_ in picked])
                print(f"  {feed}: {inbox.sum()} frames in the folded part, {n_alt} moved to another part", flush=True)
            for i, y, rl, rw in zip(idx, Y, Rl, Rws): pose[i] = (y, rw * Rot.from_matrix(rl).inv())
    for i, f in enumerate(J["frames"]):
        J["frames"][i] = {"t_ms": f["t_ms"], "ok": False, "inliers": 0}
        n = f"{feed}/{f['t_ms']:07d}.jpg"
        if i in pose:
            X, Rw = pose[i]
            q = Rw.as_quat()
            if height is not None and not lo < height(X) < hi:
                n_off += 1; continue
            if course is not None and course.query(ground(X))[0] > MAX_OFF:   # flung off the course (a few % of the map)
                n_off += 1; continue
            J["frames"][i] = {"t_ms": f["t_ms"], "ok": True, "inliers": inl[n], "center": np.round(X, 5).tolist(),
                              "qvec": [float(q[3]), float(q[0]), float(q[1]), float(q[2])], "how": "night"}; n_n += 1
    J["night_anchor"] = os.path.abspath(f"{out_dir}/transform.json")
    json.dump(J, open(f"{out_dir}/{feed}.json", "w"))
    print(f"{feed}: {n_n} of {len(J['frames'])} frames from the model ({n_off} off the course dropped)", flush=True)
