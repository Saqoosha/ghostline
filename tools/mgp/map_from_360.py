"""A SIFT map for localize.py / register.py from a 360 walk that spirula has already solved: every registered fisheye frame
is cut into VIEWS pinhole views (straight ahead and tilted TILT degrees up / down / left / right, FOV degrees wide, SIZE px),
its mask the same way; the views keep spirula's poses (fixed), pycolmap extracts SIFT, pairs each view folder's neighbours
in time plus the vocabulary tree's look-alikes, and triangulates the points with the poses held.
Built for UDGP 2026 R3 (STUDIO HONJO SLUM), where the race DVR alone breaks into many SfM pieces indoors: the walk holds
the venue together, and the race frames are then added to this map with register.py.
  map_from_360.py <spirula dataset dir> <spirula sparse model> <out dir> <vocab tree .bin>
  env SIZE (512) FOV (80) TILT (40) STEP=all|cut|sift|tri
writes <out>/images/<lens>_<view>/<stem>.jpg, <out>/masks/..., <out>/database.db, <out>/sparse/0 (the map)."""
import os, sys, time
import numpy as np, cv2
import pycolmap as p

ds, model, out, vocab = sys.argv[1:5]
SIZE, FOV, TILT = int(os.environ.get("SIZE", 512)), float(os.environ.get("FOV", 80)), float(os.environ.get("TILT", 40))
STEP = os.environ.get("STEP", "all")
f_v = SIZE / 2 / np.tan(np.radians(FOV / 2))


def rot(axis, deg):
    a = np.radians(deg); c, s = np.cos(a), np.sin(a)
    return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])}[axis]


# view -> camera rotation (d_cam = R d_view): ahead, then tilted towards +x, -x, +y, -y of the fisheye's image
VIEWS = {"c": np.eye(3), "r": rot("y", TILT), "l": rot("y", -TILT), "d": rot("x", -TILT), "u": rot("x", TILT)}


def fisheye_map(cam, R, sx, sy):
    """remap tables from the pinhole view (R: view -> camera) into the OPENCV_FISHEYE image, theta = atan2(r, z) so rays past
    90 degrees off the axis still land; sx, sy scale the model's pixels to the image file's"""
    assert cam.model.name == "OPENCV_FISHEYE", cam.model.name
    fx, fy, cx, cy, k1, k2, k3, k4 = cam.params
    u, v = np.meshgrid(np.arange(SIZE) + 0.5, np.arange(SIZE) + 0.5)
    d = np.stack([(u - SIZE / 2) / f_v, (v - SIZE / 2) / f_v, np.ones_like(u)], -1) @ R.T
    r = np.hypot(d[..., 0], d[..., 1]); th = np.arctan2(r, d[..., 2]); t2 = th * th
    thd = th * (1 + k1 * t2 + k2 * t2 ** 2 + k3 * t2 ** 3 + k4 * t2 ** 4)
    s = np.where(r > 1e-9, thd / np.maximum(r, 1e-9), 1.0)
    return ((fx * d[..., 0] * s + cx) * sx - 0.5).astype(np.float32), ((fy * d[..., 1] * s + cy) * sy - 0.5).astype(np.float32)


t0 = time.time()
rec = p.Reconstruction(model)
img_dir, msk_dir = f"{out}/images", f"{out}/masks"
poses = {}                                                   # view image name -> cam_from_world (Rigid3d)
maps = {}
for im in rec.images.values():
    if not im.has_pose: continue
    cam = rec.cameras[im.camera_id]; lens, stem = os.path.split(im.name); stem = os.path.splitext(stem)[0]
    for k, R in VIEWS.items():
        cw = im.cam_from_world()
        Rv = R.T @ cw.rotation.matrix(); tv = R.T @ cw.translation
        poses[f"{lens}_{k}/{stem}.jpg"] = p.Rigid3d(p.Rotation3d(Rv), tv)
    if STEP not in ("all", "cut"): continue
    src = cv2.imread(f"{ds}/images/{im.name}")
    mp = f"{ds}/masks/{lens}/{stem}.png"; m = cv2.imread(mp, 0) if os.path.exists(mp) else np.full(src.shape[:2], 255, np.uint8)
    sx, sy = src.shape[1] / cam.width, src.shape[0] / cam.height
    for k, R in VIEWS.items():
        key = (im.camera_id, k)
        if key not in maps: maps[key] = fisheye_map(cam, R, sx, sy)
        mx, my = maps[key]; name = f"{lens}_{k}/{stem}.jpg"
        os.makedirs(os.path.dirname(f"{img_dir}/{name}"), exist_ok=True); os.makedirs(os.path.dirname(f"{msk_dir}/{name}"), exist_ok=True)
        cv2.imwrite(f"{img_dir}/{name}", cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT), [cv2.IMWRITE_JPEG_QUALITY, 92])
        mm = cv2.remap(m, mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        cv2.imwrite(f"{msk_dir}/{name}.png", cv2.erode(mm, np.ones((5, 5), np.uint8)))
print("views", len(poses), round(time.time() - t0), "s", flush=True)

db = f"{out}/database.db"
if STEP in ("all", "cut", "sift"):
    if os.path.exists(db): os.remove(db)
    ro = p.ImageReaderOptions(camera_model="PINHOLE", mask_path=msk_dir, camera_params=f"{f_v},{f_v},{SIZE / 2},{SIZE / 2}")
    eo = p.FeatureExtractionOptions(); eo.sift.max_num_features = 4096
    p.extract_features(db, img_dir, camera_mode=p.CameraMode.PER_FOLDER, reader_options=ro, extraction_options=eo)
    print("extract", round(time.time() - t0), "s", flush=True)
    so = p.SequentialPairingOptions(); so.overlap = 10
    p.match_sequential(db, pairing_options=so)
    vo = p.VocabTreePairingOptions(); vo.vocab_tree_path = vocab; vo.num_images = 30
    p.match_vocabtree(db, pairing_options=vo)
    print("match", round(time.time() - t0), "s", flush=True)

# the poses into a reconstruction whose ids are the database's, then triangulate with them held
d = p.Database.open(db)
# only views with a verified pair: the incremental mapper's database cache leaves the others out, and register.py's
# mapper then fails on the model's ids it cannot find (unordered_map::at)
pids, geoms = d.read_two_view_geometries(); connected = set()
for pid, g in zip(pids, geoms):
    if len(g.inlier_matches) >= 15: connected.update(p.pair_id_to_image_pair(pid))
tri = p.Reconstruction()
for c in d.read_all_cameras(): tri.add_camera_with_trivial_rig(c)
n = 0
for im in d.read_all_images():
    if im.name not in poses or im.image_id not in connected: continue
    tri.add_image_with_trivial_frame(p.Image(name=im.name, camera_id=im.camera_id, image_id=im.image_id), poses[im.name]); n += 1
d.close()
print("posed views", n, flush=True)
os.makedirs(f"{out}/sparse/0", exist_ok=True)
res = p.triangulate_points(tri, db, img_dir, f"{out}/sparse/0", clear_points=True)
print("triangulate", round(time.time() - t0), "s", res.summary(), flush=True)
