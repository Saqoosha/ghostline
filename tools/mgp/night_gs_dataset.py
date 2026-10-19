"""A spirula training folder for a night 3DGS from the joint evening model (night_sfm_hloc.py): the model moved onto the
day map's coordinates (night_anchor.py's transform), so the scan goes through to_metric.py / ply_to_web.py like the day
one and lands on the same course; only the frames of the night feeds are kept (twilight frames would mix two skies).
images/<feed> and masks/<feed> are symlinks (masks at the frames' size: 720x540), sparse/0 the trimmed model.
The folder must be on a filesystem that takes symlinks (ext4; ntfs3 stopped creating them after 41).
Frames the joint model put far off the course (night frames of a dark sky: 13-40 m up or under the ground) are dropped:
env FRAME / FIT (top_frame.json, fit01.png.json: to_metric's chain) and the height range MIN_UP..MAX_UP (-1..12 m);
with COURSE (a glob of the qualifiers' pose files) also frames over MAX_OFF (6) m sideways from their paths.
With FIELD (night_warp.py's field.npz) the cameras and points also take the local correction, and FOLD (page world
x0,x1,z0,z1) drops the frames of a part the map folded: for training on the day scan's geometry (spirula --init-ply).
LOC (a folder of loc jsons re-solved on the scan, night_refine.py then cpr_gap.py) replaces a feed's frames with every
frame solved there, poses as they are (already on the day map: no field, kept in the folded part too).
STATIC (0.3) drops frames whose view barely changes in half a second (that share of the unmasked pixels within 5
levels): the finish cards drawn over the feeds, which the scan otherwise learns as a billboard, and parked quads.
usage: night_gs_dataset.py <model dir> <transform.json> <images root> <masks root> <out dir> <feed prefix> ..."""
import glob, json, os, sys
import cv2
import numpy as np
import pycolmap as p
from scipy.spatial.transform import Rotation as Rot

model, tf, iroot, mroot, out = sys.argv[1:6]; prefixes = tuple(sys.argv[6:])
T = json.load(open(tf)); A, b = np.array(T["A"]), np.array(T["b"])
s = np.cbrt(np.linalg.det(A))
R = p.Reconstruction(model)
R.transform(p.Sim3d(s, p.Rotation3d(A / s), b))                # model -> day map
feeds = sorted({im.name.split("/")[0] for im in R.images.values() if im.name.split("/")[0].startswith(prefixes)})
drop = [im for im in R.images.values() if im.name.split("/")[0] not in feeds]
for im in drop:
    R.deregister_frame(im.frame_id)
drop = []
if os.environ.get("FIELD"):                              # night_warp.py's local field: cameras and points onto the day map
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import night_warp
    from scipy.spatial.transform import Rotation as Rot
    fld = night_warp.Field(np.load(os.environ["FIELD"]))
    ims = [im for im in R.images.values() if im.has_pose]
    Y, j, _ = fld.apply(np.array([im.projection_center() for im in ims]))
    for im, y, jj in zip(ims, Y, j):
        Rcw = im.cam_from_world().rotation.matrix() @ fld.f["R"][jj].T
        R.frames[im.frame_id].rig_from_world = p.Rigid3d(p.Rotation3d(Rcw), -Rcw @ y)
    pids = list(R.points3D.keys())
    for a in range(0, len(pids), 200000):
        chunk = pids[a:a + 200000]
        Yp, _, _ = fld.apply(np.array([R.points3D[i].xyz for i in chunk]))
        for i, y in zip(chunk, Yp): R.points3D[i].xyz = y
    print(f"{len(ims)} cameras and {len(pids)} points moved by the local field")
loc_feeds = set()
if os.environ.get("LOC"):
    def rigid(f):                                        # loc json frame (cam_from_world w x y z, centre) -> Rigid3d
        w, x, y, z = f["qvec"]; Rcw = Rot.from_quat([x, y, z, w]).as_matrix()
        return p.Rigid3d(p.Rotation3d(Rcw), -Rcw @ np.array(f["center"]))
    by_name = {im.name: im for im in R.images.values()}
    next_id = max(R.images.keys()) + 1
    for feed in feeds:
        path = f"{os.environ['LOC']}/{feed}.json"
        if not os.path.exists(path): continue
        loc_feeds.add(feed)
        cam = next(im.camera_id for im in R.images.values() if im.name.startswith(feed + "/"))
        solved = {f"{feed}/{f['t_ms']:07d}.jpg": f for f in json.load(open(path))["frames"] if f["ok"]}
        kept = added = 0
        for name, im in by_name.items():
            if name.startswith(feed + "/") and im.has_pose:
                if name in solved: R.frames[im.frame_id].rig_from_world = rigid(solved[name]); kept += 1
                else: R.deregister_frame(im.frame_id)
        for name, f in solved.items():
            if name not in by_name and os.path.exists(f"{iroot}/{name}"):
                R.add_image_with_trivial_frame(p.Image(name=name, camera_id=cam, image_id=next_id), rigid(f)); next_id += 1; added += 1
        print(f"{feed}: {kept} model frames re-posed and {added} added from {path}")
if os.environ.get("FRAME"):
    F, Tf = json.load(open(os.environ["FRAME"])), json.load(open(os.environ["FIT"]))
    E, a0 = np.array([F["ex"], F["ey"], F["up"]]), np.array(F["origin"])
    lo, hi = float(os.environ.get("MIN_UP", -1)), float(os.environ.get("MAX_UP", 12))
    U, R2 = float(Tf["scale"]), np.array(Tf["R"]) @ np.diag([1, Tf["mirror"]])
    G = lambda X: (X - a0) @ E.T * U
    near = lambda X: True
    if os.environ.get("COURSE"):                         # sideways from the qualifiers' paths (page world pose files)
        import glob
        from scipy.spatial import cKDTree
        Wq = np.concatenate([np.array([f["center"] for f in json.load(open(p_))["frames"]]) for p_ in glob.glob(os.environ["COURSE"])])
        tree, MAX_OFF = cKDTree(np.c_[Wq[:, 0], -Wq[:, 2]]), float(os.environ.get("MAX_OFF", 6))
        near = lambda X: tree.query(G(X)[:2] @ R2.T + Tf["t"])[0] <= MAX_OFF
    off = [im for im in R.images.values() if im.has_pose and im.name.split("/")[0] in feeds
           and not (lo < G(im.projection_center())[2] < hi and near(im.projection_center()))]
    print(f"{len(off)} frames off the course (height outside {lo}..{hi} m) dropped")
    drop += off
    if os.environ.get("FOLD"):                           # page world x0,x1,z0,z1: a part the map folded (its poses are wrong)
        x0, x1, z0, z1 = [float(v) for v in os.environ["FOLD"].split(",")]
        def inbox(X):
            g = G(X)[:2] @ R2.T + Tf["t"]                    # track frame (x, VD z); page world z = -VD z
            return x0 < g[0] < x1 and z0 < -g[1] < z1
        fold = [im for im in R.images.values() if im.has_pose and im not in drop and im.name.split("/")[0] not in loc_feeds
                and inbox(im.projection_center())]
        print(f"{len(fold)} frames in the folded part dropped")
        drop += fold
if os.environ.get("STATIC"):
    thr, gone, still = float(os.environ["STATIC"]), {im.image_id for im in drop}, []
    on_disk = {f: sorted(int(os.path.basename(x)[:-4]) for x in glob.glob(f"{iroot}/{f}/*.jpg")) for f in feeds}
    look = lambda name: cv2.GaussianBlur(cv2.imread(f"{iroot}/{name}", cv2.IMREAD_GRAYSCALE), (5, 5), 0).astype(np.int16)
    for im in R.images.values():
        if not im.has_pose or im.image_id in gone: continue
        feed, fn = im.name.split("/"); t = int(fn[:-4]); ts = on_disk[feed]
        k = np.searchsorted(ts, t + 450)                 # the frame half a second later
        if k == len(ts) or ts[k] > t + 1500: continue
        m = cv2.imread(f"{mroot}/{im.name}.png", cv2.IMREAD_GRAYSCALE)
        d = np.abs(look(im.name) - look(f"{feed}/{ts[k]:07d}.jpg"))
        if (d[m > 0] if m is not None else d).__lt__(5).mean() > thr: still.append(im)
    print(f"{len(still)} frames whose view barely changes in half a second dropped")
    drop += still
for im in drop:
    if im.has_pose: R.deregister_frame(im.frame_id)
os.makedirs(f"{out}/images", exist_ok=True); os.makedirs(f"{out}/masks", exist_ok=True)
for f in feeds:
    for sub, root in (("images", iroot), ("masks", mroot)):
        link = f"{out}/{sub}/{f}"
        if not os.path.islink(link):
            os.symlink(os.path.realpath(f"{root}/{f}"), link)
os.makedirs(f"{out}/sparse/0", exist_ok=True)
R.write(f"{out}/sparse/0")
print(f"{len(feeds)} feeds, {R.num_reg_images()} frames ({len(drop)} other frames dropped), {R.num_points3D()} points, "
      f"scale {s:.4f} -> {out}")
