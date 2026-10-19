"""Inputs for a night race the night map has no frame of (night_race.sh): masks at the frames' size (masks720, nearest),
a camera per feed and a loc json template per feed (the frame list, all ok: false), for night_localize.py's QCAM_FROM
and night_anchor.py's day loc dirs. The camera is the night map's median for the feed's cell: the cells differ
systematically in the 4-up recordings of 10/17 (the fourth cell's focal length 274-278, the others 300-318).
usage (rt4090, in night/): night_race_prep.py <night model> <out dir> <feed> ...   (frames in sk/images/<feed>, masks in sk/masks)"""
import json, os, sys
import cv2, numpy as np, pycolmap

model, out = sys.argv[1:3]; feeds = sys.argv[3:]
rec = pycolmap.Reconstruction(model)
by_cell, seen = {}, {}
for im in rec.images.values():
    feed = im.name.split("/")[0]
    if feed.startswith(("e1", "e2", "fa_", "fb_", "na11_")) and feed not in seen:   # the 10/17 evening and night feeds
        seen[feed] = rec.cameras[im.camera_id]
for feed, cam in seen.items():
    by_cell.setdefault(feed.rsplit("_s", 1)[1], []).append(np.array(cam.params, float))
for d in ("qcam", "tmpl"): os.makedirs(f"{out}/{d}", exist_ok=True)
for feed in feeds:
    cell = feed.rsplit("_s", 1)[1]
    f, cx, cy, k = np.median(by_cell[cell], axis=0)
    names = sorted(n for n in os.listdir(f"sk/images/{feed}") if n.endswith(".jpg"))
    h, w = cv2.imread(f"sk/images/{feed}/{names[0]}").shape[:2]
    cam = {"model": "OPENCV_FISHEYE", "width": w, "height": h, "params": [float(f), float(f), w / 2, h / 2, float(k), 0.0, 0.0, 0.0],
           "from": f"night map median of cell {cell}"}
    json.dump({"camera": {**cam, "model": "SIMPLE_RADIAL_FISHEYE", "params": [float(f), w / 2, h / 2, float(k)]}}, open(f"{out}/qcam/{feed}.json", "w"))
    json.dump({"camera": cam, "frames": [{"t_ms": int(n[:-4]), "ok": False} for n in names]}, open(f"{out}/tmpl/{feed}.json", "w"))
    os.makedirs(f"sk/masks720/{feed}", exist_ok=True); n = 0
    for m in os.listdir(f"sk/masks/{feed}"):
        if not os.path.exists(f"sk/masks720/{feed}/{m}"):
            cv2.imwrite(f"sk/masks720/{feed}/{m}", cv2.resize(cv2.imread(f"sk/masks/{feed}/{m}", 0), (w, h), interpolation=cv2.INTER_NEAREST)); n += 1
    print(f"{feed}: {len(names)} frames, camera f {f:.1f} k {k:.4f} (cell {cell}), {n} masks resized", flush=True)
