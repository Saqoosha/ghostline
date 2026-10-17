"""Register footage into a fixed map after dropping its long-range self matches (look-alike gates in different places):
the footage keeps only neighbour pairs among itself (SEQ_MAX frames apart in name order) and its retrieval pairs with
the map, then the incremental mapper continues the map with the map's frames fixed (as register.py does).
usage: fly_register.py <db with map + footage, matched by register.py MAP=0> <image dir> <map model> <out model> <prefix>"""
import os, sqlite3, subprocess, sys
import pycolmap as p

db, img, model_in, model_out, pre = sys.argv[1:6]
seq = int(os.environ.get("SEQ_MAX", "40"))
work = db.replace(".db", f"_seq{seq}.db")
if not os.path.exists(work):
    subprocess.run(["cp", "-c", db, work], check=True)   # an APFS clone: instant, no extra space
    c = sqlite3.connect(work)
    fly = {i: n for i, n in c.execute("select image_id, name from images where name like ?", (pre + "%",))}
    order = {i: k for k, i in enumerate(sorted(fly, key=fly.get))}
    far = [(pid,) for (pid,) in c.execute("select pair_id from two_view_geometries")
           if pid // 2147483647 in order and pid % 2147483647 in order
           and abs(order[pid // 2147483647] - order[pid % 2147483647]) > seq]
    c.executemany("delete from two_view_geometries where pair_id = ?", far)
    c.executemany("delete from matches where pair_id = ?", far)
    c.execute("delete from rigs where ref_sensor_id not in (select camera_id from cameras)")   # the mapper refuses a rig
    c.execute("delete from rig_sensors where rig_id not in (select rig_id from rigs)")         # whose camera is gone
    c.commit(); c.close()
    print(f"dropped {len(far)} footage pairs more than {seq} frames apart", flush=True)
o = p.IncrementalPipelineOptions()
o.fix_existing_frames = True
o.multiple_models = False
o.mapper.abs_pose_min_num_inliers = int(os.environ.get("MIN_INL", 30))
os.makedirs(model_out, exist_ok=True)
recs = p.incremental_mapping(work, img, model_out, o, input_path=model_in)
r = recs[0] if 0 in recs else max(recs.values(), key=lambda r: r.num_reg_images())
n = sum(im.name.startswith(pre) for im in r.images.values())
print(f"registered {n} footage images; model {r.num_reg_images()} images", flush=True)
