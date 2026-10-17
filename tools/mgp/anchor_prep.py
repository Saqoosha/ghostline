"""Data for the anchor-clicking page (anchor.html): footage keyframes, each with the map images it matched best
(verified pairs in the big database), and every map image's keypoints that carry a map 3D point.
usage: anchor_prep.py <db> <map model> <map image root> <prefix> <out dir> [every_s=4] [fps=15]
RENAME "r01_:r1034_" maps a model name to its file on disk."""
import json, os, shutil, sqlite3, sys, collections
import numpy as np
import pycolmap as p

db, map_dir, root, pre, out = sys.argv[1:6]
every = float(sys.argv[6]) if len(sys.argv) > 6 else 4
fps = float(sys.argv[7]) if len(sys.argv) > 7 else 15
ren = dict(x.split(":") for x in os.environ.get("RENAME", "r01_:r1034_").split() if x)
M = p.Reconstruction(map_dir)
mimg = {im.image_id: im for im in M.images.values()}
c = sqlite3.connect(db)
fly = {i: n for i, n in c.execute("select image_id, name from images where name like ?", (pre + "%",))}
cand = collections.defaultdict(list)                          # footage image id -> [(rows, map image id)]
for pid, rows in c.execute("select pair_id, rows from two_view_geometries where rows >= 12"):
    a, b = pid // 2147483647, pid % 2147483647
    if a in fly and b in mimg:
        cand[a].append((rows, b))
    elif b in fly and a in mimg:
        cand[b].append((rows, a))
by_name = sorted(cand, key=lambda i: fly[i])
idx = {fly[i]: k for k, i in enumerate(sorted(fly, key=fly.get))}
step = int(every * fps)
picked, last = [], -10 ** 9
for i in by_name:                                             # the first matched frame in each `every` seconds
    k = idx[fly[i]]
    if k - last >= step:
        picked.append(i); last = k
os.makedirs(f"{out}/map", exist_ok=True)
frames = []
for i in picked:
    cs = []
    for rows, m in sorted(cand[i], reverse=True)[:6]:
        im = mimg[m]; disk = im.name
        for a, b in ren.items():
            if disk.startswith(a):
                disk = b + disk[len(a):]
        flat = im.name.replace("/", "__")
        shutil.copyfile(f"{root}/{disk}", f"{out}/map/{flat}")
        pts = [[round(float(q.xy[0]), 1), round(float(q.xy[1]), 1), int(q.point3D_id)] for q in im.points2D if q.has_point3D()]
        cs.append({"map": flat, "rows": rows, "w": M.cameras[im.camera_id].width, "h": M.cameras[im.camera_id].height, "pts": pts})
    frames.append({"fly": fly[i].split("/", 1)[1], "k": idx[fly[i]], "cands": cs})
json.dump({"frames": frames}, open(f"{out}/anchors_in.json", "w"))
print(f"{len(frames)} keyframes, {sum(len(f['cands']) for f in frames)} map images -> {out}")
