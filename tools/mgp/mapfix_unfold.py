"""Unfold the final row, which the map holds twice: two identical graphic screens stand side by side between the
TEAM BLACKSHEEP gate and NEROS, and the images that took one for the other built a second copy of the row 3.2 m to
the left (track x) of the true one. The true copy matches VelociDrone like every other gate (TBS -123.7, NEROS -113.0
= VelociDrone + the map's 0.35 m bias) but is seen by fewer images, so in the 3DGS its NEROS is the faint "ghost" right
of the solid one; the solid NEROS (-116.2) is the shifted copy's, standing where the true second screen is, so the
3DGS shows one screen and the paths, localized mostly against the shifted copy, pass 3 m inside. Every race has
images in both copies (race 1 too). Seen from a shifted-copy image (r1056_s3/0102000), the map's TBS lands on the
first screen and its screen on the second; from a true-copy one (r1243_s7/0037000), the map's NEROS leg lands on the
second screen.
Placing the images again against the map with one side's points removed does not tell the copies apart (2026-10-18:
0.00 m median for every set tried, race-1-only points, a 45 x 50 m box cleared, either side cleared): an image comes
back to the pose its own copy gives it, or fails, or lands 65 m off on another stretch with the same prints.
What does: the tall gates' upper storeys stand above the screens, so there the copies do not overlap in x.

  unfold <in_model> <out_model> <top_frame.json> <fit.json>
      labels each image in CBOX by the upper gate parts it observes (TRUE_PARTS / SHIFT_PARTS); the shifted copy's
      images move by SHIFT (track frame, m), the undecided ones leave the map (<out_model>_out.json), every point in
      ROW goes, the moved images lose their observations, the images in CBOX are triangulated again from the
      database's matches and bundle-adjusted with a RING m ring of images around CBOX held fixed (intrinsics fixed),
      observations over 4 px dropped, twice. Writes <out_model> and <out_model>_moves.json (every labelled image's
      centre before and after, page world frame: how far each copy moved).
  stage_out <unfolded model> <dir> / addback <unfolded model> <original model> <loc dir> <out model> ...
      the images unfold took out, placed against the unfolded map with localize.py and put back (those that land
      where they were or SHIFT from there; farther is the same print elsewhere)
The rounds' paths are then localized again near the row against the result (relocate_row.py / relocate_all.sh).
  prune <in_model> <out_dir> <top_frame.json> <fit.json>
      (the experiments above) deletes every point in PBOX (";"-separated boxes; MBOX ones are moved by SHIFT
      first), stages the images whose camera is in CBOX under <out_dir>/images (masks under <out_dir>/masks,
      centres in <out_dir>/staged.json) and takes them out of the map, for localize.py to place against the rest.
Boxes in to_metric.py's track frame (x = VelociDrone x, y = VelociDrone z, z = up): "x0,x1,y0,y1,z0,z1".
"""
import json, os, sys, time
import numpy as np
import pycolmap as p

t0 = time.time()
log = lambda s: print(f"[{time.time() - t0:7.0f}s] {s}", flush=True)
box = lambda k, d: [float(v) for v in os.environ.get(k, d).split(",")]
boxes = lambda k, d: [[float(v) for v in b.split(",")] for b in os.environ.get(k, d).split(";") if b]
PBOX = boxes("PBOX", "-121.5,-104,-4,20,-1.5,8")        # one or more boxes, ";"-separated
MBOX = boxes("MBOX", "")                                  # prune: points here are moved by SHIFT first
CBOX = box("CBOX", "-131,-104,-8,22,-2,15")
ROW = box("ROW", "-131,-104,-4,20,-1.5,8")
SHIFT = box("SHIFT", "3.2,0,0")


def frame(frame_json, fit_json):
    F, T = json.load(open(frame_json)), json.load(open(fit_json))
    E, a0 = np.array([F["ex"], F["ey"], F["up"]]), np.array(F["origin"])
    R2 = np.array(T["R"]) @ np.diag([1, T["mirror"]])

    def metric(X):
        G = (np.atleast_2d(X) - a0) @ E.T * T["scale"]
        return np.c_[G[:, :2] @ R2.T + T["t"], G[:, 2]]

    def to_map(d):                                   # a track-frame displacement -> the model's
        return np.r_[np.asarray(d[:2]) @ R2, d[2]] @ E / T["scale"]
    return metric, to_map


def inside(P, b):
    return (P[:, 0] > b[0]) & (P[:, 0] < b[1]) & (P[:, 1] > b[2]) & (P[:, 1] < b[3]) & (P[:, 2] > b[4]) & (P[:, 2] < b[5])


def clear(r, metric, b):
    ids = np.array(list(r.points3D))
    P = metric(np.array([r.points3D[i].xyz for i in ids]))
    for pid in ids[inside(P, b)]:
        r.delete_point3D(int(pid))
    return int(inside(P, b).sum())


def prune(model_in, out, frame_json, fit_json):
    metric, to_map = frame(frame_json, fit_json)
    r = p.Reconstruction(model_in)
    log(f"{model_in}: {r.num_reg_images()} images, {r.num_points3D()} points")
    for b in MBOX:            # e.g. the map's NEROS gate, put where VelociDrone has it, as the reference to place against
        ids = np.array(list(r.points3D))
        P = metric(np.array([r.points3D[i].xyz for i in ids]))
        for pid in ids[inside(P, b)]:
            r.points3D[int(pid)].xyz = r.points3D[int(pid)].xyz + to_map(np.array(SHIFT))
        log(f"{int(inside(P, b).sum())} points in {b} moved by {SHIFT} m")
    for b in PBOX:
        log(f"{clear(r, metric, b)} points in {b} deleted")
    src = os.environ.get("IMAGES", os.path.expanduser("~/mgp/w15/images"))
    ren = dict(x.split(":") for x in os.environ.get("RENAME", "r01_:r1034_").split() if x)    # model folder -> on disk
    disk = lambda d: next((b + d[len(a):] for a, b in ren.items() if d.startswith(a)), d)
    staged = {}
    for i in list(r.reg_image_ids()):
        im = r.images[i]
        if not inside(metric(im.projection_center()), CBOX)[0]:
            continue
        d, f = im.name.split("/")
        for kind, name, base in (("images", f, src), ("masks", f + ".png", os.path.join(os.path.dirname(src), "masks"))):
            os.makedirs(f"{out}/{kind}/{d}", exist_ok=True)
            if not os.path.lexists(f"{out}/{kind}/{d}/{name}"):
                os.symlink(os.path.realpath(f"{base}/{disk(d)}/{name}"), f"{out}/{kind}/{d}/{name}")
        staged[im.name] = metric(im.projection_center())[0].round(4).tolist()
        r.deregister_frame(im.frame_id)
    json.dump(staged, open(f"{out}/staged.json", "w"))
    log(f"staged and took out {len(staged)} images (camera box {CBOX}): {r.num_reg_images()} images, {r.num_points3D()} points left")
    os.makedirs(f"{out}/sparse/0", exist_ok=True)
    r.write(f"{out}/sparse/0")
    db = os.environ.get("DB", os.path.expanduser("~/mgp/mapfinal/database.db"))   # the features the model was built from
    if not os.path.exists(f"{out}/database.db"):
        os.symlink(os.path.realpath(db), f"{out}/database.db")


# The tall gates' upper storeys stand above the screens, so there the two copies do not overlap in x (track frame,
# measured on mapfix_neros 2026-10-18: TBS legs -125.0 / -122.4 and NEROS legs -114.3 / -111.75 in the true copy,
# NEROS legs -117.5 / -114.9 and a faint TBS -128.2 / -125.6 in the copy 3.2 m to the left). Only the parts that
# belong to one copy alone: a gate's outer leg and the middle of its top bar ((x0, x1, z0, z1), row plane).
TRUE_PARTS = [(-122.9, -121.8, 2.3, 4.6), (-124.6, -122.9, 3.6, 4.6), (-112.3, -111.2, 2.3, 4.6), (-114.0, -112.3, 3.6, 4.6)]
SHIFT_PARTS = [(-128.8, -127.6, 2.3, 4.6), (-127.6, -125.9, 3.6, 4.6), (-118.0, -117.0, 2.3, 4.6), (-117.0, -115.3, 3.6, 4.6)]


def unfold(model_in, out, frame_json, fit_json):
    """Each image in CBOX is labelled by the gate parts it observes: "true" (>= 5, 3x the other copy's) stays,
    "shift" moves by SHIFT; "mixed", and "none" right of the TBS gate (x > -121.5, where the copies overlap at screen
    height), leave the map, to be placed again against the result; "none" left of it stays."""
    RING = float(os.environ.get("RING", 20))
    metric, to_map = frame(frame_json, fit_json)
    shift = np.array(SHIFT); dmap = to_map(shift)
    r = p.Reconstruction(model_in)
    log(f"{model_in}: {r.num_reg_images()} images, {r.num_points3D()} points")
    ids = np.array(list(r.points3D)); P = metric(np.array([r.points3D[i].xyz for i in ids]))
    plane = (P[:, 1] > 5.6) & (P[:, 1] < 8.1)
    part = lambda L: set(ids[plane & np.any([(P[:, 0] > a) & (P[:, 0] < b) & (P[:, 2] > z0) & (P[:, 2] < z1) for a, b, z0, z1 in L], axis=0)].tolist())
    true_pts, shift_pts = part(TRUE_PARTS), part(SHIFT_PARTS)
    log(f"gate parts: {len(true_pts)} points in the true copy, {len(shift_pts)} in the shifted one")
    old, label = {}, {}
    for i in list(r.reg_image_ids()):
        im = r.images[i]; c = metric(im.projection_center())[0]
        if not inside(c[None], CBOX)[0]:
            continue
        obs = {pt.point3D_id for pt in im.points2D if pt.has_point3D()}
        nt, ns = len(obs & true_pts), len(obs & shift_pts)
        label[i] = ("true" if nt >= 5 and nt >= 3 * ns else "shift" if ns >= 5 and ns >= 3 * nt else
                    "mixed" if nt + ns >= 5 else "none" if c[0] > -121.5 else "true")
        old[i] = c
    log("labels: " + ", ".join(f"{k} {sum(v == k for v in label.values())}" for k in ("true", "shift", "mixed", "none")))
    for x0 in range(-132, -102, 4):
        L = [label[i] for i, c in old.items() if x0 <= c[0] < x0 + 4]
        if L:
            log(f"  x {x0:5d}..{x0 + 4:5d}: " + ", ".join(f"{k} {L.count(k)}" for k in ("true", "shift", "mixed", "none")))
    log(f"{clear(r, metric, ROW)} points in {ROW} deleted")
    moved, gone = [], []
    for i, k in label.items():
        if k in ("mixed", "none"):
            r.deregister_frame(r.images[i].frame_id); gone.append(r.images[i].name); continue
        if k != "shift":
            continue
        for j in r.images[i].get_observation_point2D_idxs():
            if r.images[i].points2D[j].has_point3D():
                r.delete_observation(i, j)
        R, c = r.images[i].cam_from_world().rotation, r.images[i].projection_center() + dmap
        r.frames[r.images[i].frame_id].rig_from_world = p.Rigid3d(R, -R.matrix() @ c)
        moved.append(i)
    log(f"{len(moved)} images of the shifted copy moved by {SHIFT} m, {len(gone)} taken out")
    json.dump(gone, open(out.rstrip("/") + "_out.json", "w"))
    reg = set(r.reg_image_ids())
    C = {i: metric(r.images[i].projection_center())[0] for i in reg}
    inner = [i for i, c in C.items() if inside(c[None], CBOX)[0]]
    big = [CBOX[0] - RING, CBOX[1] + RING, CBOX[2] - RING, CBOX[3] + RING, CBOX[4] - RING, CBOX[5] + RING]
    ring = [i for i, c in C.items() if inside(c[None], big)[0] and i not in set(inner)]
    db = p.Database.open(os.environ.get("DB", os.path.expanduser("~/mgp/mapfinal/database.db")))
    co = p.DatabaseCacheOptions(); co.image_names = {r.images[i].name for i in inner + ring}; co.min_num_matches = 15
    cache = p.DatabaseCache.create(db, co)
    inner = [i for i in inner if cache.correspondence_graph.exists_image(i)]
    log(f"{len(inner)} images in the box, {len(ring)} in the ring; database cache {cache.num_images()} images")
    obs = p.ObservationManager(r)
    tri = p.IncrementalTriangulator(cache.correspondence_graph, r, obs)
    to = p.IncrementalTriangulatorOptions()
    o = p.BundleAdjustmentOptions()
    o.refine_focal_length = o.refine_extra_params = o.refine_principal_point = False
    o.print_summary = False
    for rnd in range(int(os.environ.get("ROUNDS", 2))):
        # triangulate_image also continues and merges tracks; complete_tracks / merge_tracks are left out, they look
        # observations of images outside the cache up in the graph and throw (unordered_map::at)
        n = sum(tri.triangulate_image(to, i) for i in inner)
        log(f"round {rnd + 1}: triangulated {n} observations")
        tri.clear_modified_points3D()
        cfg = p.BundleAdjustmentConfig()
        for i in inner + ring:
            cfg.add_image(i)
        for i in ring:
            cfg.set_constant_rig_from_world_pose(r.images[i].frame_id)
        for c in {r.images[i].camera_id for i in inner + ring}:
            cfg.set_constant_cam_intrinsics(c)
        p.create_default_bundle_adjuster(o, cfg, r).solve()
        f = obs.filter_all_points3D(4.0, 1.5)
        r.update_point_3d_errors()
        log(f"  BA: filtered {f} observations; {r.num_points3D()} points, mean reprojection error {r.compute_mean_reprojection_error():.3f} px")
    os.makedirs(out, exist_ok=True)
    r.write(out)
    log(f"{out}: {r.num_reg_images()} images, {r.num_points3D()} points")
    world = lambda v: [round(float(v[0]), 3), round(float(v[2]), 3), round(float(-v[1]), 3)]   # page frame: y up, z = -VD z
    reg = set(r.reg_image_ids())
    moves = [[r.images[i].name, world(old[i]), world(metric(r.images[i].projection_center())[0])] for i in label if i in reg]
    json.dump({"note": "row images, centre before and after mapfix_unfold.py (true copy ~0, shifted copy ~SHIFT), page world frame",
               "shift": SHIFT, "moves": moves}, open(out.rstrip("/") + "_moves.json", "w"))
    log(f"{len(moves)} moves -> {out.rstrip('/')}_moves.json")


def stage(names, out):
    """symlink the listed map images (and masks) under <out>/images|masks/<folder> for localize.py; links <out>/sparse/0
    and <out>/database.db are the caller's business"""
    src = os.environ.get("IMAGES", os.path.expanduser("~/mgp/w15/images"))
    ren = dict(x.split(":") for x in os.environ.get("RENAME", "r01_:r1034_").split() if x)    # model folder -> on disk
    disk = lambda d: next((b + d[len(a):] for a, b in ren.items() if d.startswith(a)), d)
    for n in names:
        d, f = n.split("/")
        for kind, name, base in (("images", f, src), ("masks", f + ".png", os.path.join(os.path.dirname(src), "masks"))):
            os.makedirs(f"{out}/{kind}/{d}", exist_ok=True)
            if not os.path.lexists(f"{out}/{kind}/{d}/{name}"):
                os.symlink(os.path.realpath(f"{base}/{disk(d)}/{name}"), f"{out}/{kind}/{d}/{name}")


def stage_out(model, out):
    """stage the images unfold took out (<model>_out.json) against <model>: <out>/sparse/0 -> <model>, the database"""
    stage(json.load(open(model.rstrip("/") + "_out.json")), out)
    os.makedirs(f"{out}/sparse", exist_ok=True)
    for link, target in ((f"{out}/sparse/0", os.path.abspath(model)),
                         (f"{out}/database.db", os.path.realpath(os.environ.get("DB", os.path.expanduser("~/mgp/mapfinal/database.db"))))):
        if not os.path.lexists(link):
            os.symlink(target, link)
    log(f"staged {len(json.load(open(model.rstrip('/') + '_out.json')))} images under {out}")


def addback(model_in, orig_model, loc_dir, out, frame_json, fit_json):
    """put the images unfold took out back where localize.py placed them against the unfolded map (ok, >= MIN_INL
    inliers), keypoints from the original model, then triangulate and bundle-adjust as unfold does; appends their
    moves (centre in the original model -> now) to <out>_moves.json"""
    MIN_INL, RING = int(os.environ.get("MIN_INL", 30)), float(os.environ.get("RING", 20))
    # an image belongs either where it was or SHIFT from there; farther means the same print elsewhere on the course
    # (2026-10-18: 30 of 751 landed 20-66 m off)
    MAX_MOVE = float(os.environ.get("MAX_MOVE", 5))
    metric, _ = frame(frame_json, fit_json)
    r, o0 = p.Reconstruction(model_in), p.Reconstruction(orig_model)
    log(f"{model_in}: {r.num_reg_images()} images, {r.num_points3D()} points")
    names = set(json.load(open(model_in.rstrip("/") + "_out.json")))
    by0 = {im.name: im for im in o0.images.values()}
    added, before = [], {}
    for fn in sorted(os.listdir(loc_dir)):
        if not fn.endswith(".json"):
            continue
        for f in json.load(open(f"{loc_dir}/{fn}"))["frames"]:
            n = "%s/%07d.jpg" % (fn[:-5], f["t_ms"])
            if n not in names or not f["ok"] or f["inliers"] < MIN_INL or n not in by0:
                continue
            im0 = by0[n]
            q, c = np.array(f["qvec"]), np.array(f["center"])
            if np.linalg.norm(metric(c)[0] - metric(im0.projection_center())[0]) > MAX_MOVE:
                continue
            R = p.Rotation3d(np.r_[q[1:], q[0]])                     # localize.py writes w,x,y,z
            im = p.Image(name=n, camera_id=im0.camera_id, image_id=im0.image_id, points2D=[p.Point2D(pt.xy) for pt in im0.points2D])
            r.add_image_with_trivial_frame(im, p.Rigid3d(R, -R.matrix() @ c))
            added.append(im0.image_id); before[im0.image_id] = metric(im0.projection_center())[0]
    log(f"{len(added)} of {len(names)} taken-out images placed with >= {MIN_INL} inliers and added")
    reg = set(r.reg_image_ids())
    C = {i: metric(r.images[i].projection_center())[0] for i in reg}
    inner = [i for i, c in C.items() if inside(c[None], CBOX)[0]]
    big = [CBOX[0] - RING, CBOX[1] + RING, CBOX[2] - RING, CBOX[3] + RING, CBOX[4] - RING, CBOX[5] + RING]
    ring = [i for i, c in C.items() if inside(c[None], big)[0] and i not in set(inner)]
    db = p.Database.open(os.environ.get("DB", os.path.expanduser("~/mgp/mapfinal/database.db")))
    co = p.DatabaseCacheOptions(); co.image_names = {r.images[i].name for i in inner + ring}; co.min_num_matches = 15
    cache = p.DatabaseCache.create(db, co)
    inner = [i for i in inner if cache.correspondence_graph.exists_image(i)]
    obs = p.ObservationManager(r)
    tri = p.IncrementalTriangulator(cache.correspondence_graph, r, obs)
    to = p.IncrementalTriangulatorOptions()
    o = p.BundleAdjustmentOptions()
    o.refine_focal_length = o.refine_extra_params = o.refine_principal_point = False
    o.print_summary = False
    for rnd in range(int(os.environ.get("ROUNDS", 2))):
        n = sum(tri.triangulate_image(to, i) for i in (added if rnd == 0 else inner) if i in set(inner))
        log(f"round {rnd + 1}: triangulated {n} observations")
        cfg = p.BundleAdjustmentConfig()
        for i in inner + ring:
            cfg.add_image(i)
        for i in ring:
            cfg.set_constant_rig_from_world_pose(r.images[i].frame_id)
        for c in {r.images[i].camera_id for i in inner + ring}:
            cfg.set_constant_cam_intrinsics(c)
        p.create_default_bundle_adjuster(o, cfg, r).solve()
        f = obs.filter_all_points3D(4.0, 1.5)
        r.update_point_3d_errors()
        log(f"  BA: filtered {f} observations; {r.num_points3D()} points, mean reprojection error {r.compute_mean_reprojection_error():.3f} px")
    os.makedirs(out, exist_ok=True)
    r.write(out)
    log(f"{out}: {r.num_reg_images()} images, {r.num_points3D()} points")
    world = lambda v: [round(float(v[0]), 3), round(float(v[2]), 3), round(float(-v[1]), 3)]
    M = json.load(open(model_in.rstrip("/") + "_moves.json"))
    reg = set(r.reg_image_ids())
    M["moves"] += [[r.images[i].name, world(before[i]), world(metric(r.images[i].projection_center())[0])] for i in added if i in reg]
    json.dump(M, open(out.rstrip("/") + "_moves.json", "w"))
    d = np.array([metric(r.images[i].projection_center())[0] - before[i] for i in added if i in reg])
    log(f"{len(d)} added images moved: dx histogram (-inf -2 -0.5 0.5 2 4 inf) {np.histogram(d[:, 0], [-99, -2, -0.5, 0.5, 2, 4, 99])[0].tolist()}")


if __name__ == "__main__":
    {"prune": prune, "unfold": unfold, "stage_out": stage_out, "addback": addback}[sys.argv[1]](*sys.argv[2:])
