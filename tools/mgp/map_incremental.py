"""Incremental mapper over an already matched database (sfm_big.py STEP=match), on every EVERY-th frame of each
pilot folder. The global mapper fell apart on 23k uncalibrated fisheye frames (only shards); the incremental one
refines each pilot's intrinsics as it grows, which is how the race-1 map came out whole.
usage: map_incremental.py <work_dir> <out_dir> [every=2]"""
import os, sys, time
import pycolmap as p

w, out = sys.argv[1], sys.argv[2]
every = int(sys.argv[3]) if len(sys.argv) > 3 else 2
names = []
for d in sorted(os.listdir(f"{w}/images")):
    fs = sorted(f for f in os.listdir(f"{w}/images/{d}") if f.endswith(".jpg"))
    names += [f"{d}/{f}" for f in fs[::every]]
print(len(names), "images", flush=True)
t = time.time()
o = p.IncrementalPipelineOptions()
o.image_names = names
o.multiple_models = True
o.num_threads = -1
os.makedirs(out, exist_ok=True)
recs = p.incremental_mapping(f"{w}/database.db", f"{w}/images", out, o)
print("map", round(time.time() - t), "s", flush=True)
for k, r in recs.items():
    print(k, r.num_reg_images(), "images", r.num_points3D(), "points")
