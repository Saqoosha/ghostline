"""usage: rt_synth_views.py scan_cameras.json out.json  (then pass out.json to rt_map.py next to scan_cameras.json)
Course-agnostic keyframe poses: a grid over the ground the scan walked, at racing heights, looking down in 12 directions.
Written as a poses file rt_map.py reads (src "ba"). web frame: x east, y up, z south; quat (x,y,z,w) world-from-camera, COLMAP axes.
env: STEP (4 m) grid spacing; HEIGHTS (0.6,2.1 m above the knee-height walk); PITCHES (10,35 deg down); WALK (off) keep only grid
     points within this many m of the walk. The map's descriptors all sit on the GPU (~0.26 MB a view), so 24 GB holds ~60k views"""
import json, os, sys, numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation as Rot
fl = lambda k, d: [float(v) for v in os.environ.get(k, d).split(",")]
STEP, HEIGHTS, PITCHES, WALK = fl("STEP", "4")[0], fl("HEIGHTS", "0.6,2.1"), np.radians([-p for p in fl("PITCHES", "10,35")]), float(os.environ.get("WALK", "inf"))
cams = json.load(open(sys.argv[1]))["cameras"]; ground = [c["pos"] for c in cams if c["clip"] != "dji"]
g = np.array(ground); x0, x1, z0, z1 = g[:, 0].min(), g[:, 0].max(), g[:, 2].min(), g[:, 2].max()
knee = np.median(g[:, 1]); walk = cKDTree(g[:, [0, 2]])
poses = []
for x in np.arange(x0, x1 + 1e-6, STEP):
    for z in np.arange(z0, z1 + 1e-6, STEP):
        if walk.query([x, z])[0] > WALK: continue
        for h in HEIGHTS:
            for yaw, PITCH in [(y, p) for y in np.radians(np.arange(0, 360, 30)) for p in PITCHES]:
                f = np.array([np.cos(PITCH) * np.sin(yaw), np.sin(PITCH), np.cos(PITCH) * np.cos(yaw)])   # forward
                down = np.array([0, -1.0, 0]); yv = down - f * (down @ f); yv /= np.linalg.norm(yv); xv = np.cross(yv, f)
                poses.append({"pos": [round(float(x), 3), round(float(knee + h), 3), round(float(z), 3)],
                              "quat": Rot.from_matrix(np.stack([xv, yv, f], axis=1)).as_quat().round(6).tolist(), "src": "ba"})
json.dump({"poses": poses}, open(sys.argv[2], "w")); print(len(poses), "synthetic views")
