"""A spirula training folder for a night 3DGS from the joint evening model (night_sfm_hloc.py): the model moved onto the
day map's coordinates (night_anchor.py's transform), so the scan goes through to_metric.py / ply_to_web.py like the day
one and lands on the same course; only the frames of the night feeds are kept (twilight frames would mix two skies).
images/<feed> and masks/<feed> are symlinks (masks at the frames' size: 720x540), sparse/0 the trimmed model.
The folder must be on a filesystem that takes symlinks: not rt4090's /mnt/data (ntfs3 stopped creating them after 41).
Frames the joint model put far off the course (night frames of a dark sky: 13-40 m up or under the ground) are dropped:
env FRAME / FIT (top_frame.json, fit01.png.json: to_metric's chain) and the height range MIN_UP..MAX_UP (-1..12 m);
with COURSE (a glob of the qualifiers' pose files) also frames over MAX_OFF (6) m sideways from their paths.
usage: night_gs_dataset.py <model dir> <transform.json> <images root> <masks root> <out dir> <feed prefix> ..."""
import json, os, sys
import numpy as np
import pycolmap as p

model, tf, iroot, mroot, out = sys.argv[1:6]; prefixes = tuple(sys.argv[6:])
T = json.load(open(tf)); A, b = np.array(T["A"]), np.array(T["b"])
s = np.cbrt(np.linalg.det(A))
R = p.Reconstruction(model)
R.transform(p.Sim3d(s, p.Rotation3d(A / s), b))                # model -> day map
feeds = sorted({im.name.split("/")[0] for im in R.images.values() if im.name.split("/")[0].startswith(prefixes)})
drop = [im for im in R.images.values() if im.name.split("/")[0] not in feeds]
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
    off = [im for im in R.images.values() if im.name.split("/")[0] in feeds
           and not (lo < G(im.projection_center())[2] < hi and near(im.projection_center()))]
    print(f"{len(off)} frames off the course (height outside {lo}..{hi} m) dropped")
    drop += off
for im in drop:
    R.deregister_frame(im.frame_id)
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
