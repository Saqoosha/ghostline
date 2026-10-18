"""Night SfM with learned features (hloc on rt4090, ~/mastenv). Night feeds are full of the codec's coloured specks and
SIFT on CLAHE frames left 445 of A Main Race 11's 2,471 frames in the largest model. On night pairs 0.5-2 s apart
ALIKED + LightGlue gave the most F-matrix inliers, most at full size (720x540, fullres_frames.py) with 4,096 keypoints;
CLAHE and median / morphology filters against the specks made ALIKED worse, so the frames go in as they are.
Pairs: sequential within a feed (OVERLAP frames, then powers of two up to 64 frames) and MegaLoc retrieval (NRET)
across feeds and laps. The OSD masks (extract.py, 480x360) drop keypoints, scaled to the frame and eroded. Mapping:
pycolmap incremental, one camera per feed (OPENCV_FISHEYE, refined).
usage: night_sfm_hloc.py <out dir> <images root> <masks root> <feed>[:<every n-th frame>] ...
  frames: <images root>/<feed>/<t_ms>.jpg, masks <masks root>/<feed>/<t_ms>.jpg.png
writes <out dir>/: feats.h5, global.h5, pairs.txt, matches.h5, database.db, models/<k>/ (every model, largest first)
env: MAXK (4096), OVERLAP (10), NRET (30), CAM (OPENCV_FISHEYE params at the frames' size, 720x540 default),
  DB (database file name), MODELS (output folder name), SNAPSHOT (incremental: a partial model every n frames, in
  <models>_snap/), BA_ITERS (global BA iterations), PRUNE_BA=1 (global BA skips redundant points), INPUT (a model to
  continue from, e.g. a snapshot),
  MAPPER (incremental | hierarchical: models_hierarchical/ | global: GLOMAP, models_global/), REUSE_DB=1 (map again from
  the verified database.db). GLOMAP failed on the evening feeds twice: 1.1 px, right rotations, but every feed's cameras
  collapsed to within ~3 steps of one point (night_model_check.py: 13-17% of the steps are jumps)"""
import os, shutil, sys, time
from pathlib import Path
import cv2, h5py, numpy as np
import pycolmap
from hloc import extract_features, match_features, pairs_from_retrieval

MAXK = int(os.environ.get("MAXK", 4096))
CONF = {"output": "feats-aliked", "model": {"name": "aliked", "model_name": "aliked-n16", "max_num_keypoints": MAXK,
        "detection_threshold": 0.01}, "preprocessing": {"grayscale": False, "resize_max": 1024}}
CAM = os.environ.get("CAM", "357,357,360,270,0,0,0,0")
MAPPER = os.environ.get("MAPPER", "incremental")
t0 = time.time()
log = lambda s: print(f"[{time.time() - t0:6.0f}s] {s}", flush=True)


def frame_list(img_root, specs):
    """feed[:step] ... -> relative frame names"""
    names = []
    for spec in specs:
        feed, _, step = spec.partition(":")
        names += [f"{feed}/{f}" for f in sorted(os.listdir(img_root / feed)) if f.endswith(".jpg")][::int(step or 1)]
    return names


def extract_masked(img_root, msk_root, names, path):
    """ALIKED features of names into path (h5), keypoints under the OSD masks dropped"""
    extract_features.main(CONF, img_root, image_list=names, feature_path=path)
    kept = []
    with h5py.File(path, "r+") as fd:
        for n in names:
            g = fd[n]; k = g["keypoints"][()]; N = len(k)
            h, w = g["image_size"][()][::-1]
            m = cv2.resize(cv2.imread(str(msk_root / f"{n}.png"), 0), (int(w), int(h)), interpolation=cv2.INTER_NEAREST)
            m = cv2.erode((m > 0).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
            keep = m[np.clip(k[:, 1].round().astype(int), 0, m.shape[0] - 1), np.clip(k[:, 0].round().astype(int), 0, m.shape[1] - 1)]
            for key in list(g.keys()):
                d = g[key]
                if d.ndim == 0 or key == "image_size":
                    continue
                ax = [i for i, s in enumerate(d.shape) if s == N]
                if not ax:
                    continue
                attrs, v = dict(d.attrs), np.compress(keep, d[()], axis=ax[0])
                del g[key]; g.create_dataset(key, data=v)
                for a, b in attrs.items(): g[key].attrs[a] = b
            kept.append(keep.mean())
    log(f"features: {len(names)} frames, masks kept {np.mean(kept):.0%} of the keypoints")


def main(out, img_root, msk_root, specs):
    from hloc.reconstruction import create_empty_db, get_image_ids
    from hloc.triangulation import import_features, import_matches, estimation_and_geometric_verification
    OVERLAP, NRET = int(os.environ.get("OVERLAP", 10)), int(os.environ.get("NRET", 30))
    out.mkdir(parents=True, exist_ok=True)
    names = frame_list(img_root, specs)
    feeds = [s.partition(":")[0] for s in specs]
    log(f"{len(names)} frames in {len(feeds)} feeds")
    feats = out / "feats.h5"
    if not feats.exists():
        extract_masked(img_root, msk_root, names, feats)
    # ---- pairs: sequential + retrieval
    pairs = out / "pairs.txt"
    if not pairs.exists():
        P = set()
        for c in feeds:
            fr = [n for n in names if n.startswith(c + "/")]
            steps = list(range(1, OVERLAP + 1)) + [2 ** j for j in range(4, 7) if 2 ** j > OVERLAP]
            for i in range(len(fr)):
                for s in steps:
                    if i + s < len(fr): P.add((fr[i], fr[i + s]))
        log(f"{len(P)} sequential pairs")
        gfeats = out / "global.h5"
        if not gfeats.exists():
            extract_features.main(extract_features.confs["megaloc"], img_root, image_list=names, feature_path=gfeats)
        rp = out / "pairs_ret.txt"
        pairs_from_retrieval.main(gfeats, rp, num_matched=NRET, db_list=names, query_list=names)
        for line in open(rp):
            a, b = line.split()
            if a != b and (b, a) not in P: P.add((a, b))
        open(pairs, "w").write("".join(f"{a} {b}\n" for a, b in sorted(P)))
        log(f"{len(P)} pairs with retrieval")
    # ---- matching
    matches = out / "matches.h5"
    match_features.main(match_features.confs["aliked+lightglue"], pairs, features=feats, matches=matches)
    log("matching")
    # ---- database and mapping
    db = out / os.environ.get("DB", "database.db")
    if not (db.exists() and os.environ.get("REUSE_DB") == "1"):
        if db.exists(): db.unlink()
        create_empty_db(db)
        ro = pycolmap.ImageReaderOptions(); ro.camera_model = "OPENCV_FISHEYE"; ro.camera_params = CAM
        pycolmap.import_images(db, img_root, pycolmap.CameraMode.PER_FOLDER, image_names=names, options=ro)
        ids = get_image_ids(db)
        with pycolmap.Database.open(db) as d:
            import_features(ids, d, feats)
            import_matches(ids, d, pairs, matches, None, False)
        estimation_and_geometric_verification(db, pairs, False)
        log("database")
    models = out / os.environ.get("MODELS", "models_" + MAPPER if MAPPER != "incremental" else "models")
    shutil.rmtree(models, ignore_errors=True); models.mkdir()
    if MAPPER == "hierarchical":
        # clusters of LEAF frames mapped in parallel (incremental, fewer global BAs), then merged
        h = pycolmap.HierarchicalPipelineOptions(); h.num_workers = int(os.environ.get("WORKERS", 4))
        h.clustering_options.leaf_max_num_images = int(os.environ.get("LEAF", 800)); h.clustering_options.image_overlap = 80
        io = h.incremental_options; io.num_threads = int(os.environ.get("THREADS", 4))
        io.mapper.init_min_num_inliers = 50; io.mapper.abs_pose_min_num_inliers = 20
        io.ba_global_frames_ratio = io.ba_global_points_ratio = 1.4
        io.ba_global_frames_freq, io.ba_global_points_freq = 100000, 100000000
        recs = pycolmap.hierarchical_mapping(db, img_root, models, h)
    elif MAPPER == "global":
        # GLOMAP: rotation averaging and global positioning, then one BA. Incremental mapping on race 11's ALIKED tracks
        # (4,096 keypoints a frame, ~1,000 points seen per frame) spent 10+ minutes in each global BA
        g = pycolmap.GlobalPipelineOptions(); g.multiple_models = True; g.num_threads = 16
        recs = pycolmap.global_mapping(db, img_root, models, g)
    else:
        o = pycolmap.IncrementalPipelineOptions(); o.multiple_models = True; o.num_threads = 16
        o.mapper.init_min_num_inliers = 50; o.mapper.abs_pose_min_num_inliers = 20
        # fewer global BAs (each took 10+ minutes on race 11's ALIKED tracks once the model had ~1,000 frames)
        r = float(os.environ.get("BA_RATIO", 1.1))
        o.ba_global_frames_ratio = o.ba_global_points_ratio = r
        if r > 1.1: o.ba_global_frames_freq, o.ba_global_points_freq = 100000, 100000000
        if os.environ.get("BA_ITERS"): o.ba_global_max_num_iterations = int(os.environ["BA_ITERS"])
        # global BA on the points that add coverage only: at 2,071 frames one global BA took over 30 minutes
        o.mapper.ba_global_ignore_redundant_points3D = os.environ.get("PRUNE_BA") == "1"
        if os.environ.get("SNAPSHOT"):                       # partial models to check while it runs
            o.snapshot_path = str(models) + "_snap"; o.snapshot_frames_freq = int(os.environ["SNAPSHOT"])
            os.makedirs(o.snapshot_path, exist_ok=True)
        recs = pycolmap.incremental_mapping(db, img_root, models, o, input_path=os.environ.get("INPUT", ""))
    log("mapping")
    for k, r in sorted(recs.items(), key=lambda kv: -kv[1].num_reg_images()):
        per = {c: sum(1 for im in r.images.values() if im.name.startswith(c + "/")) for c in feeds}
        log(f"model {k}: {r.num_reg_images()} frames, {r.num_points3D()} points, {r.compute_mean_reprojection_error():.2f} px, {per}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:])
