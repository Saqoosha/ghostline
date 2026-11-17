"""UDGP 2026 R3: candidate keyframe poses for rt_map.py from localize.py's answers (every feed of the day), in rt_map's
poses format: {"poses": [{"quat": world-from-camera x,y,z,w, "pos": camera centre, "src": "ba"}]}, map frame (z up, metres).
  uv run --with numpy --with scipy python tools/dvr/udgp_kf_poses.py <loc dir> <out.json> [MIN_INL (40)]
Only answers with MIN_INL inliers or more go in: a keyframe at a wrong pose would put the wrong 3D points on the map."""
import glob, json, sys
import numpy as np
from scipy.spatial.transform import Rotation as Rot

loc, out = sys.argv[1], sys.argv[2]; MIN_INL = int(sys.argv[3]) if len(sys.argv) > 3 else 40
poses = []
for f in sorted(glob.glob(f"{loc}/*.json")):
    for fr in json.load(open(f))["frames"]:
        if not fr["ok"] or fr["inliers"] < MIN_INL: continue
        w, x, y, z = fr["qvec"]                                  # cam_from_world
        q = Rot.from_quat([x, y, z, w]).inv().as_quat()          # world_from_cam
        poses.append(dict(quat=[round(float(v), 6) for v in q], pos=fr["center"], src="ba"))
json.dump(dict(poses=poses), open(out, "w"))
print(len(poses), "poses with", MIN_INL, "or more inliers")
