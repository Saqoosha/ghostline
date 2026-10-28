"""Bring other footage (e.g. the official fly-through) into the map without per-frame registration: registered frame by
frame against the map it collapsed onto look-alike places, because few of its frames match the map at all. Instead
  1. its own SfM (the footage matches itself well), from the features and matches register.py already put in <db>;
  2. one similarity onto the map, by RANSAC over 3D-3D pairs: a footage point and a map point are a pair when their
     keypoints were matched (verified) between a footage image and a map image;
  3. a merged text model for training: the map as it is, plus the footage's cameras and images with no 2D points.
usage: fly_align.py <db with map + footage> <map model> <prefix e.g. fly/> <work dir>"""
import os, shutil, sqlite3, sys, collections
import numpy as np
import pycolmap as p

db, map_dir, pre, w = sys.argv[1:5]
os.makedirs(w, exist_ok=True)
M = p.Reconstruction(map_dir)
SCALE_M = float(os.environ.get("UNIT_M", "7.37"))   # metres per map unit, for reporting only

# 1. the footage alone
fdb = f"{w}/fly.db"
if not os.path.exists(f"{w}/sfm/0/images.bin"):
    shutil.copyfile(db, fdb)
    c = sqlite3.connect(fdb)
    c.execute("create temp table k(id integer primary key)")
    c.execute("insert into k select image_id from images where name like ?", (pre + "%",))
    for t in ("keypoints", "descriptors"):
        c.execute(f"delete from {t} where image_id not in (select id from k)")
    for t in ("matches", "two_view_geometries"):
        c.execute(f"delete from {t} where (pair_id / 2147483647) not in (select id from k) or (pair_id % 2147483647) not in (select id from k)")
    c.execute("delete from frame_data where data_id not in (select id from k)")
    c.execute("delete from frames where frame_id not in (select frame_id from frame_data)")
    c.execute("delete from images where image_id not in (select id from k)")
    c.execute("delete from cameras where camera_id not in (select camera_id from images)")   # and their rigs: the mapper
    c.execute("delete from rigs where ref_sensor_id not in (select camera_id from cameras)")  # refuses a rig whose camera is gone
    c.execute("delete from rig_sensors where rig_id not in (select rig_id from rigs)")
    # only neighbours in time: retrieval pairs join look-alike gates in different places (identical prints)
    order = {i: n for n, (i,) in enumerate(c.execute("select image_id from images order by name"))}
    seq = int(os.environ.get("SEQ_MAX", "15"))
    far = [(pid,) for (pid,) in c.execute("select pair_id from two_view_geometries")
           if abs(order[pid // 2147483647] - order[pid % 2147483647]) > seq]
    c.executemany("delete from two_view_geometries where pair_id = ?", far)
    c.executemany("delete from matches where pair_id = ?", far)
    print(f"dropped {len(far)} pairs more than {seq} frames apart")
    c.commit(); c.execute("vacuum"); c.close()
    o = p.IncrementalPipelineOptions()
    os.makedirs(f"{w}/sfm", exist_ok=True)
    p.incremental_mapping(fdb, f"{os.path.dirname(os.path.abspath(db))}/images", f"{w}/sfm", o)
models = sorted((p.Reconstruction(f"{w}/sfm/{d}") for d in os.listdir(f"{w}/sfm") if d.isdigit()), key=lambda r: -r.num_reg_images())
print("footage models:", [r.num_reg_images() for r in models])

# 2. 3D-3D pairs through the verified footage-map matches in the big database
c = sqlite3.connect(db)
names = {i: n for i, n in c.execute("select image_id, name from images")}
mapimg = {im.image_id: im for im in M.images.values()}


def pairs_for(F):
    fimg = {im.name: im for im in F.images.values()}
    by_id = {i: fimg[n] for i, n in names.items() if n in fimg}           # big-db id -> footage image (same keypoints)
    votes = collections.Counter()
    for pid, rows, data in c.execute("select pair_id, rows, data from two_view_geometries where rows > 0"):
        a, b = pid // 2147483647, pid % 2147483647
        if a in by_id and b in mapimg:
            fi, mi, flip = by_id[a], mapimg[b], False
        elif b in by_id and a in mapimg:
            fi, mi, flip = by_id[b], mapimg[a], True
        else:
            continue
        if data is None:
            continue
        mm = np.frombuffer(data, np.uint32).reshape(rows, 2)
        if flip:
            mm = mm[:, ::-1]
        for kf, km in mm:
            pf, pm = fi.points2D[kf], mi.points2D[km]
            if pf.has_point3D() and pm.has_point3D():
                votes[(pf.point3D_id, pm.point3D_id)] += 1
    return votes


def sim3(A, B):
    """Umeyama: s, R, t with B ~ s R A + t."""
    ma, mb = A.mean(0), B.mean(0); a, b = A - ma, B - mb
    U, S, Vt = np.linalg.svd(b.T @ a / len(A))
    D = np.eye(3); D[2, 2] = np.sign(np.linalg.det(U @ Vt))
    R = U @ D @ Vt; s = np.trace(np.diag(S) @ D) / a.var(0).sum()
    return s, R, mb - s * R @ ma


rng = np.random.default_rng(0)
merged = []
for F in models:
    if F.num_reg_images() < 10:
        continue
    v = pairs_for(F)
    if len(v) < 20:
        print(f"model of {F.num_reg_images()}: {len(v)} pairs, skipped"); continue
    keys = list(v); A = np.array([F.points3D[a].xyz for a, _ in keys]); B = np.array([M.points3D[b].xyz for _, b in keys])
    thr = float(os.environ.get("THR_M", "0.5")) / SCALE_M
    best = (0, None)
    for _ in range(20000):
        idx = rng.choice(len(A), 4, replace=False)
        s, R, t = sim3(A[idx], B[idx])
        if not (1e-3 < s < 1e3):
            continue
        n = int((np.linalg.norm((s * (R @ A.T)).T + t - B, axis=1) < thr).sum())
        if n > best[0]:
            best = (n, (s, R, t))
    n, (s, R, t) = best
    inl = np.linalg.norm((s * (R @ A.T)).T + t - B, axis=1) < thr
    s, R, t = sim3(A[inl], B[inl])
    res = np.linalg.norm((s * (R @ A[inl].T)).T + t - B[inl], axis=1) * SCALE_M
    print(f"model of {F.num_reg_images()} images: {len(keys)} pairs, {inl.sum()} inliers, residual p50 {np.median(res):.2f} m p90 {np.percentile(res, 90):.2f} m")
    if inl.sum() < 15:
        continue
    F.transform(p.Sim3d(s, p.Rotation3d(R), t))
    merged.append(F)

# 3. the map plus the footage's cameras and images (no 2D points), as a text model
out = f"{w}/merged"; os.makedirs(out, exist_ok=True)
M.write_text(out)
cid = max(M.cameras) + 1; iid = max(M.images) + 1
with open(f"{out}/cameras.txt", "a") as fc, open(f"{out}/images.txt", "a") as fi:
    for F in merged:
        cmap = {}
        for k, cam in F.cameras.items():
            cmap[k] = cid
            fc.write(f"{cid} {cam.model.name} {cam.width} {cam.height} {' '.join(repr(float(x)) for x in cam.params)}\n"); cid += 1
        for im in sorted(F.images.values(), key=lambda i: i.name):
            q = im.cam_from_world().rotation.quat; tt = im.cam_from_world().translation    # quat is x y z w
            fi.write(f"{iid} {q[3]} {q[0]} {q[1]} {q[2]} {tt[0]} {tt[1]} {tt[2]} {cmap[im.camera_id]} {im.name}\n\n"); iid += 1
print(f"merged: {len(M.images)} map + {sum(F.num_reg_images() for F in merged)} footage images -> {out}")
