"""Localize every frame of a pilot's feed against an existing SfM map, without changing the map.

Per frame: OpenCV SIFT under the OSD/prop mask (its descriptors match COLMAP's after RootSIFT, and
it is ~10x faster than pycolmap's CPU SIFT), then 2D-3D matches against the 3D points seen by the
K map images nearest the constant-velocity prediction (position + viewing direction) plus the
previous frame's inliers, then LO-RANSAC PnP + refinement with the pilot's camera. The first frame,
any frame where that fails, and frames more than LOST_MS after the last good one relocalize
globally: match against one descriptor per map point (~0.4 s), vote map images through the
matched points, and solve as above with the top K.
The map side (descriptors of the triangulated keypoints, image poses) is cached in
<map>/loc_cache.npz on the first run.

usage: localize.py <map_dir> <out_dir> <images/folder>... [-j N] [--cam folder=map_folder]...
                   [--lens folder=stock|upgrade|median|auto]...
  <map_dir> holds database.db and sparse/0; masks are read from <images>/../masks/<folder>.
  --cam: use a map pilot's refined camera for this folder (q01_s0=r01_s0); a folder whose name is
  in the map uses its own. Any other folder takes a lens (--lens, else env LENS, default auto):
  the HDZero Nano 90 stock or upgrade lens, the median map camera, or auto = the one with the most
  inliers on three trial windows. Then the focal length is refined on the first FOCAL_N good
  frames and fixed after. The JSON's camera carries "lens" (and "lens_trial" for auto).
  env HOLDOUT_MS: for tests, skip map images of the same pilot within this many ms of the frame.
writes <out_dir>/<folder>.json: {"camera", "frames": [{"t_ms", "ok", "center", "qvec", "inliers"}]}
(qvec = cam_from_world [w,x,y,z], map coordinates). One process per folder, each on one thread.
"""
import os
os.environ.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
import ctypes, json, sys, time
from multiprocessing import Pool
try:    # keep the per-frame match matrices on the heap: mmap/munmap per frame costs page faults
    _libc = ctypes.CDLL("libc.so.6")
    _libc.mallopt(-3, 1 << 30); _libc.mallopt(-1, 1 << 30)    # M_MMAP_THRESHOLD, M_TRIM_THRESHOLD
except OSError:
    pass
import numpy as np, cv2
import pycolmap as p

K = int(os.environ.get("K", 6))                  # map images per frame
MIN_INL = int(os.environ.get("MIN_INL", 20))      # inliers to accept a pose
LOST_MS = int(os.environ.get("LOST_MS", 500))     # gap after which the prediction is dropped
RELOC_MAX = int(os.environ.get("RELOC_MAX", 0))   # >0: while lost, a failed global search waits 1, 2, 4 .. this many frames
FOCAL_N = int(os.environ.get("FOCAL_N", 15))        # 0: keep the lens focal length
HOLDOUT_MS = int(os.environ.get("HOLDOUT_MS", -1))
RATIO, MAXD = 0.8, 0.7                            # COLMAP's SIFT matching defaults
VIEW_W = 2.0                                      # map units per (1 - cos) of viewing-direction difference
cv2.setNumThreads(1)
SIFT = cv2.SIFT_create(4096, contrastThreshold=float(os.environ.get("CT", 0.02)))   # 0.02 ~ COLMAP peak_threshold x 3


def unit(d):
    d = d.astype(np.float32)
    return d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)


def build_cache(m):
    path = f"{m}/loc_cache.npz"
    if os.path.exists(path):
        return path
    r, db = p.Reconstruction(f"{m}/sparse/0"), p.Database.open(f"{m}/database.db")
    names, C, V, off, pid, desc = [], [], [], [0], [], []
    for i in sorted(r.images):
        im = r.images[i]
        k = np.array([j for j, q in enumerate(im.points2D) if q.has_point3D()], int)
        names.append(im.name); C.append(im.projection_center()); V.append(im.viewing_direction())
        off.append(off[-1] + len(k))
        pid.append(np.array([im.points2D[j].point3D_id for j in k], np.int64))
        desc.append(db.read_descriptors(i).data[k])
    pid, desc = np.concatenate(pid), np.concatenate(desc)
    pts = np.unique(pid)
    P = np.array([r.points3D[q].xyz for q in pts.tolist()])
    row = np.searchsorted(pts, pid)                       # observation -> point row
    # one descriptor per point for global relocalization: the observation closest to the mean
    order = np.argsort(row, kind="stable")
    starts = np.r_[0, np.nonzero(np.diff(row[order]))[0] + 1]
    df = unit(desc[order])
    mean = np.add.reduceat(df, starts)
    near = np.array([s + np.argmax(df[s:e] @ mu) for s, e, mu in zip(starts, np.r_[starts[1:], len(df)], mean)])
    cams = {c.camera_id: [c.model.name, c.width, c.height, list(c.params)] for c in r.cameras.values()}
    pilot_cam = {im.name.split("/")[0]: im.camera_id for im in r.images.values()}
    tmp = f"{path}.{os.getpid()}.npz"
    np.savez(tmp, names=np.array(names), C=np.array(C), V=np.array(V), off=np.array(off), row=row, desc=desc,
             P=P, P_desc=desc[order[near]], meta=json.dumps({"cams": cams, "pilot_cam": pilot_cam}))
    os.replace(tmp, path)
    return path


def match(qd, md):
    """query (n,128) vs map (m,128), unit vectors, one map row per point: ratio test, one query per point."""
    if len(md) < 2 or not len(qd):
        return np.zeros((0, 2), int)
    n = np.arange(len(qd))
    s1, s2, b = np.full(len(qd), -2, np.float32), np.full(len(qd), -2, np.float32), np.zeros(len(qd), int)
    for o in range(0, len(md), 8192):      # chunks keep the global (all points) match small in memory
        S = qd @ md[o:o + 8192].T
        cb = S.argmax(1)
        c1 = S[n, cb]
        c2 = np.partition(S, -2, axis=1)[:, -2] if S.shape[1] > 1 else np.full(len(qd), -2, np.float32)
        s2 = np.where(c1 > s1, np.maximum(s1, c2), np.maximum(s2, c1))
        b = np.where(c1 > s1, cb + o, b)
        s1 = np.maximum(s1, c1)
    d1, d2 = np.sqrt(np.maximum(2 - 2 * s1, 0)), np.sqrt(np.maximum(2 - 2 * s2, 0))
    q = np.nonzero((d1 < MAXD) & (d1 < RATIO * d2))[0]
    q = q[np.argsort(-s1[q])]
    _, u = np.unique(b[q], return_index=True)
    return np.c_[q[u], b[q[u]]]


class Map:
    def __init__(s, path):
        z = np.load(path)
        s.names, s.C, s.V, s.off, s.row, s.P = z["names"], z["C"], z["V"], z["off"], z["row"], z["P"]
        s.desc, s.Pd = unit(z["desc"]), unit(z["P_desc"])
        s.meta = json.loads(str(z["meta"]))
        s.pilot = np.array([n.split("/")[0] for n in s.names])
        s.ms = np.array([int(n.split("/")[1][:-4]) for n in s.names])

    def nearest(s, c, v, k, skip):
        d = np.linalg.norm(s.C - c, axis=1) + VIEW_W * (1 - s.V @ v)
        d[skip] = np.inf
        return np.argsort(d)[:k]

    def vote(s, qd, k, skip):
        """global: match against one descriptor per point, vote images through their observations."""
        mt = match(qd, s.Pd)
        hit = np.zeros(len(s.P), bool); hit[mt[:, 1]] = True
        img = np.repeat(np.arange(len(s.names)), np.diff(s.off))
        n = np.bincount(img[hit[s.row]], minlength=len(s.names)).astype(float)
        n[skip] = -1
        return np.argsort(-n)[:k] if n.max() > 0 else []

    def local(s, imgs, prev):
        """points seen by imgs (nearest first) + the previous frame's inliers; one descriptor per point."""
        sel = np.concatenate([np.arange(s.off[i], s.off[i + 1]) for i in imgs])
        d, r = np.r_[prev[0], s.desc[sel]], np.r_[prev[1], s.row[sel]]
        _, u = np.unique(r, return_index=True)
        return d[u], r[u]




# HDZero Nano 90 lenses, data/dvr/cams/hdzero-nano90-*-lens.json "source_fisheye" halved to 480x360.
# Same OPENCV_FISHEYE convention as the map: the upgrade lens projects within 1 px of the race-1 map's
# self-calibrated cameras out to 60 deg. The stock one is the DVR tools' definition (focal x1.2, k1 negated).
LENSES = {"stock": [238.03351, 237.35782, 240, 180, -0.0790836, 0, 0, 0],
          "upgrade": [198.36126, 197.79818, 240, 180, 0.0790836, -0.0031367, 0.0121730, -0.0196835]}
LENS_N = int(os.environ.get("LENS_N", 60))        # frames per auto-lens trial window (3 windows)


def lens_camera(mp, lens):
    m, w, h, _ = next(iter(mp.meta["cams"].values()))
    prm = LENSES[lens] if lens in LENSES else np.median([c[3] for c in mp.meta["cams"].values()], 0)
    return p.Camera(model=m, width=w, height=h, params=prm)


def features(fdir, files, mask):
    for f in files:
        kp, qd = SIFT.detectAndCompute(cv2.imread(f"{fdir}/{f}", cv2.IMREAD_GRAYSCALE), mask)
        xy = np.array([k.pt for k in kp], np.float64).reshape(-1, 2)
        qd = np.sqrt(qd / np.maximum(qd.sum(1, keepdims=True), 1e-9)).astype(np.float32) if qd is not None else np.zeros((0, 128), np.float32)
        yield int(f[:-4]), xy, qd


def track(mp, feats, cam, refine_f, mine):
    """localize a sequence of (t_ms, xy, desc); refine_f: refine the focal length on the first FOCAL_N good frames."""
    est, ref = p.AbsolutePoseEstimationOptions(), p.AbsolutePoseRefinementOptions()
    est.ransac.max_error = 6.0
    no_prev = (np.zeros((0, 128), np.float32), np.zeros(0, int))
    aspect, fixed = cam.params[1] / cam.params[0], not refine_f
    focals, frames, hist, prev = [], [], [], no_prev    # hist: (t, centre, viewdir) of the last good frames
    back = wait = 0                                     # global-search backoff while lost (frames)
    tm, t0 = np.zeros(2), time.time()
    for t_ms, xy, qd in feats:
        t1 = time.time()
        skip = mine & (np.abs(mp.ms - t_ms) <= HOLDOUT_MS)

        def attempt(imgs, prev):
            if not len(imgs):
                return None, [], None
            md, rows = mp.local(imgs, prev)
            mt = match(qd, md)
            if len(mt) < MIN_INL:
                return None, mt, rows
            ref.refine_focal_length = not fixed
            return p.estimate_and_refine_absolute_pose(xy[mt[:, 0]], mp.P[rows[mt[:, 1]]], cam, est, ref), mt, rows

        res = None
        if hist and t_ms - hist[-1][0] < LOST_MS:
            c, v = hist[-1][1], hist[-1][2]
            if len(hist) > 1:
                a = (t_ms - hist[-1][0]) / max(hist[-1][0] - hist[-2][0], 1)
                c, v = c + a * (c - hist[-2][1]), v + a * (v - hist[-2][2])
            res, mt, rows = attempt(mp.nearest(c, v / np.linalg.norm(v), K, skip), prev)
        else:
            hist = []
        if (res is None or res["num_inliers"] < MIN_INL) and wait > 0:
            wait -= 1                                       # backing off the global search
        elif res is None or res["num_inliers"] < MIN_INL:   # lost: relocalize globally
            prev = no_prev
            res, mt, rows = attempt(mp.vote(qd, K, skip), prev)
            if res is None or res["num_inliers"] < MIN_INL:
                back = min(max(2 * back, 1), RELOC_MAX)
                wait = back
            else:
                back = 0
        ok = res is not None and res["num_inliers"] >= MIN_INL
        rec = {"t_ms": t_ms, "ok": ok, "inliers": int(res["num_inliers"]) if res else 0}
        if ok:
            T = res["cam_from_world"]
            R = T.rotation.matrix()
            ctr, q = -R.T @ T.translation, T.rotation.quat     # quat is x,y,z,w
            rec.update(center=ctr.round(5).tolist(), qvec=[float(q[3]), float(q[0]), float(q[1]), float(q[2])])
            hist, back, wait = (hist + [(t_ms, ctr, R[2])])[-2:], 0, 0
            inl = np.asarray(res["inlier_mask"])
            prev = (qd[mt[inl, 0]], rows[mt[inl, 1]])
            if not fixed:
                focals.append(cam.params[0])
                if len(focals) >= FOCAL_N:
                    fx = float(np.median(focals))
                    cam.params = np.r_[fx, fx * aspect, cam.params[2:]]
                    fixed = True
        else:
            prev = no_prev
        frames.append(rec)
        tm[1] += time.time() - t1
    tm[0] = time.time() - t0 - tm[1]     # the rest is reading + SIFT (the generator)
    return frames, tm


def pick_lens(mp, fdir, files, mask, mine):
    """auto lens: localize LENS_N frames at 20/50/80% of the flight with each candidate (fixed intrinsics);
    the score is the mean inlier count over all trial frames, failures counting 0."""
    scores = {k: [] for k in ("stock", "upgrade", "median")}
    for at in (0.2, 0.5, 0.8):
        s = max(0, min(int(at * len(files)), len(files) - LENS_N))
        win = list(features(fdir, files[s:s + LENS_N], mask))
        for k in scores:
            scores[k] += [fr["inliers"] if fr["ok"] else 0 for fr in track(mp, win, lens_camera(mp, k), False, mine)[0]]
    stats = {k: {"score": round(float(np.mean(v)), 1), "registered": round(float(np.mean(np.array(v) > 0)), 3),
                 "median_inliers": int(np.median(v))} for k, v in scores.items()}
    return max(stats, key=lambda k: stats[k]["score"]), stats


def localize(args):
    mpath, out, fdir, cam_map, lens_map = args
    fdir = os.path.abspath(fdir)
    folder = os.path.basename(fdir)
    mp = Map(mpath)
    files = sorted(f for f in os.listdir(fdir) if f.endswith(".jpg"))
    if not files:
        return f"{folder}: no frames"
    mask = cv2.imread(f"{os.path.dirname(os.path.dirname(fdir))}/masks/{folder}/{files[0]}.png", cv2.IMREAD_GRAYSCALE)
    t_all, extra = time.time(), {}
    pilot = cam_map.get(folder, folder)
    if pilot in mp.meta["pilot_cam"]:      # this pilot is in the map: its refined camera, fixed
        m, w, h, prm = mp.meta["cams"][str(mp.meta["pilot_cam"][pilot])]
        cam, refine_f, extra["lens"] = p.Camera(model=m, width=w, height=h, params=prm), False, f"map:{pilot}"
    else:
        pilot, lens = None, lens_map.get(folder, os.environ.get("LENS", "auto"))
        if lens == "auto":
            lens, extra["lens_trial"] = pick_lens(mp, fdir, files, mask, np.zeros(len(mp.names), bool))
        cam, refine_f, extra["lens"] = lens_camera(mp, lens), FOCAL_N > 0, lens
    frames, tm = track(mp, features(fdir, files, mask), cam, refine_f, mp.pilot == pilot)
    n = max(len(frames), 1)
    camj = {"model": cam.model.name, "width": cam.width, "height": cam.height, "params": [float(x) for x in cam.params], **extra}
    json.dump({"camera": camj, "frames": frames}, open(f"{out}/{folder}.json", "w"))
    return (f"{folder} [{extra['lens']}]: {sum(fr['ok'] for fr in frames)}/{len(frames)} ok, "
            f"{1000 * (time.time() - t_all) / n:.1f} ms/frame (sift {1000 * tm[0] / n:.1f}, match+pnp {1000 * tm[1] / n:.1f})"
            + (f" trial {extra['lens_trial']}" if "lens_trial" in extra else ""))


if __name__ == "__main__":
    a, jobs, cam_map, lens_map, rest = sys.argv[1:], os.cpu_count(), {}, {}, []
    while a:
        x = a.pop(0)
        if x == "-j":
            jobs = int(a.pop(0))
        elif x in ("--cam", "--lens"):
            k, v = a.pop(0).split("=")
            (cam_map if x == "--cam" else lens_map)[k] = v
        else:
            rest.append(x)
    m, out, dirs = rest[0], rest[1], rest[2:]
    os.makedirs(out, exist_ok=True)
    path = build_cache(m)
    with Pool(min(jobs, len(dirs))) as pool:
        for s in pool.imap_unordered(localize, [(path, out, d, cam_map, lens_map) for d in dirs]):
            print(s, flush=True)
