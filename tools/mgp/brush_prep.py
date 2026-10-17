"""A COLMAP model -> a dataset Brush can train on (Brush ignores lens distortion and finds a mask by file stem alone):
every image is remapped to a pinhole camera with its own focal length, given a flat unique name (<folder>__<file>), and
its mask (COLMAP's: black = ignore) is remapped the same way, with the area outside the source frame masked too.
usage: brush_prep.py <model> <out_dir> <image_root>=<src_images>[,<mask_root>]...   (a name's first folder picks the root
by prefix: r01_=/x/images,/x/masks maps r01_s0/0000000.jpg to /x/images/r01_s0/... ; RENAME r01_:r1034_ renames on disk)"""
import os, sys
from concurrent.futures import ThreadPoolExecutor
import numpy as np, cv2
import pycolmap as p

model, out = sys.argv[1], sys.argv[2]
roots = []                                        # (name prefix, image dir, mask dir or None)
for a in sys.argv[3:]:
    pre, rest = a.split("=", 1); d = rest.split(",")
    roots.append((pre, d[0], d[1] if len(d) > 1 else None))
ren = dict(x.split(":") for x in os.environ.get("RENAME", "").split() if x)
SCALE = float(os.environ.get("SCALE", "1.15"))     # output size over input size: keeps more of the fisheye's edge


def pinhole(cam):
    """the pinhole camera an image is remapped to, and the map (output pixel -> source pixel)."""
    w, h = int(cam.width * SCALE), int(cam.height * SCALE)
    f = (cam.focal_length_x + cam.focal_length_y) / 2
    cx, cy = w / 2, h / 2
    u, v = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    x, y = (u - cx) / f, (v - cy) / f
    src = cam.img_from_cam(np.stack([x.ravel(), y.ravel(), np.ones(x.size)], 1))    # COLMAP's own projection, distortion included
    sx = src[:, 0].reshape(h, w).astype(np.float32) - 0.5; sy = src[:, 1].reshape(h, w).astype(np.float32) - 0.5
    return (w, h, f, cx, cy), sx, sy


r = p.Reconstruction(model)
os.makedirs(f"{out}/images", exist_ok=True); os.makedirs(f"{out}/masks", exist_ok=True)
maps = {cid: pinhole(c) for cid, c in r.cameras.items()}
for cid, ((w, h, f, cx, cy), _, _) in maps.items():
    r.cameras[cid] = p.Camera(camera_id=cid, model="PINHOLE", width=w, height=h, params=[f, f, cx, cy])


def one(iid):
    im = r.images[iid]; name = im.name
    root = next((x for x in roots if name.startswith(x[0])), None)
    if root is None:
        return iid, False
    disk = name
    for a, b in ren.items():
        if disk.startswith(a):
            disk = b + disk[len(a):]
    img = cv2.imread(f"{root[1]}/{disk}")
    if img is None:
        return iid, False
    _, sx, sy = maps[im.camera_id]
    flat = name.replace("/", "__")
    cv2.imwrite(f"{out}/images/{flat}", cv2.remap(img, sx, sy, cv2.INTER_LINEAR), [cv2.IMWRITE_JPEG_QUALITY, 95])
    m = np.full(img.shape[:2], 255, np.uint8)
    if root[2] and os.path.exists(f"{root[2]}/{disk}.png"):
        m = cv2.imread(f"{root[2]}/{disk}.png", cv2.IMREAD_GRAYSCALE)
    m = cv2.remap(m, sx, sy, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)   # outside the frame: masked
    cv2.imwrite(f"{out}/masks/{os.path.splitext(flat)[0]}.png", m)
    return iid, flat


with ThreadPoolExecutor(int(os.environ.get("THREADS", "8"))) as ex:
    res = list(ex.map(one, list(r.images.keys())))
missing = [iid for iid, ok in res if not ok]
for iid, flat in res:
    if flat:
        r.images[iid].name = flat
for iid in missing:
    r.deregister_frame(r.images[iid].frame_id)
print(f"{len(res) - len(missing)} images written, {len(missing)} missing (dropped from the model)")
os.makedirs(f"{out}/sparse/0", exist_ok=True)
r.write(f"{out}/sparse/0")
