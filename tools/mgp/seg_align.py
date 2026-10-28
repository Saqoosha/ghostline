"""One stretch of footage into the map: its own SfM (neighbour pairs only, short enough that the scale does not drift),
then one similarity from the hand-clicked anchors inside it (their solved centres vs. the SfM's), checked by the
anchors' residuals. Registration into the map could not hold this footage (few matches, look-alike gates).
usage: seg_align.py <db with the footage matched> <image root> <map model> <anchor dir> <out dir> <prefix> <first> <last>
writes <out>/sfm (the stretch), <out>/aligned (in map coordinates) and prints the fit"""
import json, os, shutil, sqlite3, sys
import numpy as np
import pycolmap as p

db, img_root, map_dir, adir, out, pre, f0, f1 = sys.argv[1:9]; f0, f1 = int(f0), int(f1)
UNIT = float(os.environ.get("UNIT_M", "7.37")); SEQ = int(os.environ.get("SEQ_MAX", "40"))
os.makedirs(out, exist_ok=True)
name_of = lambda k: f"{pre}f{k:05d}.jpg"

# the stretch's own database: its images, their features, and only neighbour pairs among them
sdb = f"{out}/seg.db"
if not os.path.exists(f"{out}/sfm/0/images.bin"):
    shutil.copyfile(db, sdb)
    c = sqlite3.connect(sdb)
    keep = {i: int(n[len(pre) + 1:len(pre) + 6]) for i, n in c.execute("select image_id, name from images where name like ?", (pre + "%",))
            if f0 <= int(n[len(pre) + 1:len(pre) + 6]) <= f1}
    c.execute("create temp table k(id integer primary key)"); c.executemany("insert into k values (?)", [(i,) for i in keep])
    for t in ("keypoints", "descriptors"):
        c.execute(f"delete from {t} where image_id not in (select id from k)")
    far = [(pid,) for (pid,) in c.execute("select pair_id from two_view_geometries")
           if not (pid // 2147483647 in keep and pid % 2147483647 in keep) or abs(keep[pid // 2147483647] - keep[pid % 2147483647]) > SEQ]
    c.executemany("delete from two_view_geometries where pair_id = ?", far); c.executemany("delete from matches where pair_id = ?", far)
    c.execute("delete from frame_data where data_id not in (select id from k)")
    c.execute("delete from frames where frame_id not in (select frame_id from frame_data)")
    c.execute("delete from images where image_id not in (select id from k)")
    c.execute("delete from cameras where camera_id not in (select camera_id from images)")
    c.execute("delete from rigs where ref_sensor_id not in (select camera_id from cameras)")
    c.execute("delete from rig_sensors where rig_id not in (select rig_id from rigs)")
    c.commit(); c.execute("vacuum"); c.close()
    print(f"{len(keep)} frames {f0}-{f1}", flush=True)
    o = p.IncrementalPipelineOptions()                     # several models allowed: the one holding most anchors is used
    cj = json.load(open(f"{adir}/camera.json"))                      # the anchors' calibrated camera as the start
    os.makedirs(f"{out}/sfm", exist_ok=True)
    c = sqlite3.connect(sdb); cid = c.execute("select camera_id from cameras").fetchone()[0]
    c.execute("update cameras set params = ?, prior_focal_length = 1 where camera_id = ?", (np.array(cj["params"], np.float64).tobytes(), cid)); c.commit(); c.close()
    p.incremental_mapping(sdb, img_root, f"{out}/sfm", o)
A = json.load(open(f"{adir}/anchors.json"))
models = [p.Reconstruction(f"{out}/sfm/{d}") for d in sorted(os.listdir(f"{out}/sfm")) if d.isdigit()]
print("stretch models:", [m.num_reg_images() for m in models], flush=True)
S = max(models, key=lambda m: (sum((pre + f) in {im.name for im in m.images.values()} for f in A), m.num_reg_images()))
reg = sorted(int(im.name[len(pre) + 1:len(pre) + 6]) for im in S.images.values())
print(f"stretch model: {len(reg)} frames ({reg[0]}-{reg[-1]}), {len(S.points3D)} points", flush=True)

# the anchors inside the stretch: their centres solved against the map (as on the anchor page) and in the stretch
M = p.Reconstruction(map_dir); cj = json.load(open(f"{adir}/camera.json"))
cam = p.Camera(camera_id=1, model=cj["model"], width=cj["width"], height=cj["height"], params=cj["params"])
A = json.load(open(f"{adir}/anchors.json")); byname = {im.name: im for im in S.images.values()}
src, dst, names = [], [], []
for fly, a in sorted(A.items()):
    k = int(fly[1:6]); s = a.get("solve", {})
    if not (f0 <= k <= f1) or not s.get("ok") or (s.get("wobble_m") or 99) > float(os.environ.get("MAX_WOBBLE", "0.5")) or (pre + fly) not in byname:
        continue
    P2 = np.array([q[:2] for q in a["pairs"]], float); P3 = np.array([M.points3D[int(q[2])].xyz for q in a["pairs"]])
    o = p.AbsolutePoseEstimationOptions(); o.ransac.max_error = 12.0
    r = p.estimate_and_refine_absolute_pose(P2, P3, cam, o)
    if r is None:
        continue
    T = r["cam_from_world"]; dst.append(-T.rotation.matrix().T @ T.translation)
    src.append(byname[pre + fly].projection_center()); names.append(fly)
src, dst = np.array(src), np.array(dst)
print(f"anchors in the stretch: {names}", flush=True)
if len(src) < 3:
    sys.exit("need 3+ anchors inside the stretch")


def sim3(A, B):
    ma, mb = A.mean(0), B.mean(0); a, b = A - ma, B - mb
    U, S_, Vt = np.linalg.svd(b.T @ a / len(A)); D = np.eye(3); D[2, 2] = np.sign(np.linalg.det(U @ Vt))
    R = U @ D @ Vt; s = np.trace(np.diag(S_) @ D) / a.var(0).sum(); return s, R, mb - s * R @ ma


s, R, t = sim3(src, dst)
res = np.linalg.norm((s * (R @ src.T)).T + t - dst, axis=1) * UNIT
print("anchor residuals m:", dict(zip(names, res.round(2))), flush=True)
S.transform(p.Sim3d(s, p.Rotation3d(R), t))
os.makedirs(f"{out}/aligned", exist_ok=True); S.write(f"{out}/aligned")
k = np.array(reg); C = np.array([S.images[i].projection_center() for i in sorted(S.images, key=lambda i: S.images[i].name)]) * UNIT
v = np.linalg.norm(np.diff(C, axis=0), axis=1) / (np.diff(k) / 15)
print(f"step speed m/s p50 {np.median(v):.1f} p90 {np.percentile(v, 90):.1f} max {v.max():.1f}; over 40 m/s: {(v > 40).sum()} of {len(v)}", flush=True)
