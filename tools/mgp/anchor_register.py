"""Footage frames into the fixed map, grown from hand-clicked anchors (anchor.html / anchor_server.py): the anchors enter
the map at their solved poses, points are triangulated from their matches (point_triangulator), and the incremental
mapper then registers the footage frames around them, the map and the anchors held fixed.
Per-frame registration alone failed on this footage (few matches to the map, look-alike gates); the anchors supply what
the matching cannot.
usage: anchor_register.py <db (footage + map, matched; footage long-range pairs dropped)> <footage image root>
                          <map model> <anchor dir (anchors.json, camera.json)> <out dir> <footage prefix e.g. fly15/>"""
import json, os, sqlite3, sys
import numpy as np
import pycolmap as p

db, img_root, map_dir, adir, out, pre = sys.argv[1:7]
MAX_WOBBLE = float(os.environ.get("MAX_WOBBLE", "0.35"))  # m; an anchor whose pose moves more without one pair is left out
MIN_FIT = int(os.environ.get("MIN_FIT", "4"))             # pairs within 8 px
os.makedirs(out, exist_ok=True)

R = p.Reconstruction(map_dir)
cj = json.load(open(f"{adir}/camera.json"))
c = sqlite3.connect(db)
ids = {n: (i, cid) for i, n, cid in c.execute("select image_id, name, camera_id from images where name like ?", (pre + "%",))}
cid = next(iter(ids.values()))[1]
cam = p.Camera(camera_id=cid, model=cj["model"], width=cj["width"], height=cj["height"], params=cj["params"])
R.add_camera_with_trivial_rig(cam)


def keypoints(image_id):
    rows, cols, data = c.execute("select rows, cols, data from keypoints where image_id = ?", (image_id,)).fetchone()
    return np.frombuffer(data, np.float32).reshape(rows, cols)[:, :2].astype(float)


A = json.load(open(f"{adir}/anchors.json"))
used = []
for fly, a in sorted(A.items()):
    s = a.get("solve", {})
    if not s.get("ok") or s.get("wobble_m") is None or s["wobble_m"] > MAX_WOBBLE or sum(e <= 8 for e in s["err"]) < MIN_FIT:
        continue
    P2 = np.array([q[:2] for q in a["pairs"]], float); P3 = np.array([R.points3D[int(q[2])].xyz for q in a["pairs"]])
    o = p.AbsolutePoseEstimationOptions(); o.ransac.max_error = 12.0
    r = p.estimate_and_refine_absolute_pose(P2, P3, cam, o)
    if r is None:
        continue
    name = pre + fly; iid = ids[name][0]
    im = p.Image(name=name, camera_id=cid, image_id=iid, points2D=[p.Point2D(xy) for xy in keypoints(iid)])
    R.add_image_with_trivial_frame(im, r["cam_from_world"])
    used.append((fly, s["wobble_m"]))
print(f"{len(used)} anchors: {used}", flush=True)

tri = f"{out}/tri"; os.makedirs(tri, exist_ok=True)
n0 = len(R.points3D)
R = p.triangulate_points(R, db, img_root, tri, clear_points=False)
print(f"triangulated: {n0} -> {len(R.points3D)} points", flush=True)

o = p.IncrementalPipelineOptions()
o.fix_existing_frames = True
o.multiple_models = False
o.mapper.abs_pose_min_num_inliers = int(os.environ.get("MIN_INL", 20))
reg = f"{out}/reg"; os.makedirs(reg, exist_ok=True)
recs = p.incremental_mapping(db, img_root, reg, o, input_path=tri)
R2 = recs[0] if 0 in recs else max(recs.values(), key=lambda r: r.num_reg_images())
F = sorted((im for im in R2.images.values() if im.name.startswith(pre)), key=lambda im: im.name)
print(f"registered {len(F)} footage frames ({len(used)} anchors); model {R2.num_reg_images()} images", flush=True)
