# Merge cpr_track.py runs over separate frame ranges (track_a, track_b, ...) into track/: one track.jsonl, one dump dir,
# and the init track cpr_ba.py starts from (as cpr_track.py writes it, over all frames).
import json, sys, os, shutil, numpy as np
from scipy.spatial.transform import Rotation as Rot, Slerp
cam = json.load(open("dvr_pinhole.mp4.json")); N = int(sys.argv[1]); rec = {}
os.makedirs("track/dump", exist_ok=True)
for d in sys.argv[2:]:
    for r in map(json.loads, open(f"{d}/track.jsonl")):
        rec[r["i"]] = r; shutil.copy(f"{d}/dump/{r['i']:05d}.npz", "track/dump/")
with open("track/track.jsonl", "w") as f:
    for i in sorted(rec): f.write(json.dumps(rec[i]) + "\n")
ks = sorted(rec); Pk = np.array([rec[i]["pos"] for i in ks]); sl = Slerp(ks, Rot.from_quat([rec[i]["quat"] for i in ks])); poses = []
for i in range(N):
    j = min(max(i, ks[0]), ks[-1])
    poses.append({"i": i, "t": round(cam["t0"] + i / cam["fps"], 4), "pos": np.round([np.interp(j, ks, Pk[:, a]) for a in range(3)], 3).tolist(),
                  "quat": np.round(sl([j]).as_quat()[0], 6).tolist(), "src": "track" if i in rec else "interp"})
json.dump({"frame": "the scan's frame, y up, metres; quat (x,y,z,w) world-from-camera, COLMAP camera axes",
           "t0": cam["t0"], "fps": cam["fps"], "poses": poses}, open("track/track_init.json", "w"))
print(f"merged {len(rec)} of {N} frames")
