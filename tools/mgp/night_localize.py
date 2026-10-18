"""Register every frame of night / dusk feeds in a learned-feature SfM model (night_sfm_hloc.py builds it from a subset,
e.g. every 5th frame of 15 fps feeds): ALIKED features masked like the model's, pairs = the model's frames of the same
feed within WIN s plus MegaLoc retrieval (NRET) over the model's frames, LightGlue, then hloc's localize_sfm (PnP and
refinement with the feed's camera as the model refined it). Frames in the model keep their SfM pose.
usage: night_localize.py <sfm dir> <model dir> <images root> <masks root> <out dir> <feed> ...
writes <out dir>/results.txt (name qw qx qy qz tx ty tz: cam_from_world in model coordinates, every registered frame)
and <out dir>/inliers.json (name -> PnP inliers; SfM frames: their observed 3D points)
env: WIN (1.0 s), NRET (10), THRESH (8 px, at 720x540), MIN_INL (30), QCAM_FROM (folders of loc jsons, ':'-separated)"""
import json, os, sys
from pathlib import Path
import cv2, numpy as np
import pycolmap
from hloc import extract_features, match_features, pairs_from_retrieval
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from night_sfm_hloc import extract_masked, frame_list, log

sfm, model_dir, img_root, msk_root, out = (Path(a) for a in sys.argv[1:6]); feeds = sys.argv[6:]
WIN, NRET, THRESH = float(os.environ.get("WIN", 1.0)), int(os.environ.get("NRET", 10)), float(os.environ.get("THRESH", 8))
MIN_INL = int(os.environ.get("MIN_INL", 30))
out.mkdir(parents=True, exist_ok=True)
rec = pycolmap.Reconstruction(model_dir)
reg = {im.name: im for im in rec.images.values()}
names = frame_list(img_root, feeds)
queries = [n for n in names if n not in reg]
log(f"{len(names)} frames, {len(names) - len(queries)} already in the model, {len(queries)} to register")
qfeats = out / "qfeats.h5"
if not qfeats.exists():
    extract_masked(img_root, msk_root, queries, qfeats)
# ---- pairs: the model's frames of the same feed nearby in time, and retrieval
pairs = out / "pairs.txt"
if not pairs.exists():
    tms = lambda n: int(n.split("/")[1][:-4])
    by_feed = {}
    for n in reg:
        by_feed.setdefault(n.split("/")[0], []).append((tms(n), n))
    P = set()
    for q in queries:
        P.update((q, n) for t, n in by_feed.get(q.split("/")[0], []) if abs(t - tms(q)) <= WIN * 1000)
    log(f"{len(P)} pairs in time")
    qglobal = out / "qglobal.h5"
    if not qglobal.exists():
        extract_features.main(extract_features.confs["megaloc"], img_root, image_list=queries, feature_path=qglobal)
    rp = out / "pairs_ret.txt"
    pairs_from_retrieval.main(qglobal, rp, num_matched=NRET, query_list=queries, db_list=list(reg), db_descriptors=sfm / "global.h5")
    P.update(tuple(l.split()) for l in open(rp))
    open(pairs, "w").write("".join(f"{a} {b}\n" for a, b in sorted(P)))
    log(f"{len(P)} pairs with retrieval")
matches = out / "matches.h5"
match_features.main(match_features.confs["aliked+lightglue"], pairs, features=qfeats, matches=matches, features_ref=sfm / "feats.h5")
log("matching")
# ---- localization with each feed's camera from the model (hloc's localize_sfm.main would put a frame it cannot
# solve at its first retrieved frame's pose: the pieces are called here instead)
from hloc.localize_sfm import QueryLocalizer, pose_from_cluster
from hloc.utils.parsers import parse_retrieval
cams = {}
for n, im in reg.items():
    cams.setdefault(n.split("/")[0], rec.cameras[im.camera_id])
# feeds the model has no frame of (daytime anchors such as the D final): the camera the day localizer chose for the feed
# (its loc json in a QCAM_FROM folder), scaled from its width to the frames' (480 -> 720)
for d in filter(None, os.environ.get("QCAM_FROM", "").split(":")):
    for fn in os.listdir(d):
        feed = fn[:-5]
        if fn.endswith(".json") and feed not in cams and any(q.startswith(feed + "/") for q in queries):
            c = json.load(open(f"{d}/{fn}"))["camera"]
            w = cv2.imread(str(img_root / next(q for q in queries if q.startswith(feed + "/")))).shape[1]; k = w / c["width"]
            P = list(c["params"]); P[:4] = [v * k for v in P[:4]]
            cams[feed] = pycolmap.Camera(model=c["model"], width=round(c["width"] * k), height=round(c["height"] * k), params=P)
            log(f"{feed}: camera from {d}/{fn} ({c.get('lens')}), x{k:.2f}")
localizer = QueryLocalizer(rec, {"estimation": {"ransac": {"max_error": THRESH}}})
ids = {im.name: i for i, im in rec.images.items()}
retr = parse_retrieval(pairs)
fset = {s.partition(":")[0] for s in feeds}
inl, lines = {}, []
for n, im in reg.items():
    if n.split("/")[0] in fset:
        p = im.cam_from_world(); q = p.rotation.quat                         # x y z w
        lines.append(f"{n} {q[3]} {q[0]} {q[1]} {q[2]} {' '.join(map(str, p.translation))}")
        inl[n] = sum(1 for p2 in im.points2D if p2.has_point3D())
n_loc = 0
for k, qn in enumerate(queries):
    cam, db = cams.get(qn.split("/")[0]), [ids[n] for n in retr.get(qn, []) if n in ids]
    if cam is None or not db:
        continue
    ret, _ = pose_from_cluster(localizer, qn, cam, db, qfeats, matches)
    if ret is None or ret["num_inliers"] < MIN_INL:
        continue
    p = ret["cam_from_world"]; q = p.rotation.quat
    lines.append(f"{qn} {q[3]} {q[0]} {q[1]} {q[2]} {' '.join(map(str, p.translation))}")
    inl[qn] = int(ret["num_inliers"]); n_loc += 1
    if k % 500 == 0: log(f"  {k} / {len(queries)}: {n_loc} registered")
open(out / "results.txt", "w").write("\n".join(lines) + "\n")
json.dump(inl, open(out / "inliers.json", "w"))
log(f"registered {n_loc} of {len(queries)} by localization; results.txt has {len(lines)} frames")
