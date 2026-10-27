"""The course line from the reference flight (dji_srt_track.py, ENU), with a stretch the flight cut replaced by the X6 walk
(walk_enu: the RTK SfM's rig path), and every point's height taken from the nearest walk position (the walker's camera
height: a line a steady height above the ground; the drone's rel_alt is from its own takeoff and its GPS height is metres off).
  python course_splice.py <ref.json> <walk.json> <out.json> <ref cut a:b> <walk keep a:b> [STEP=0.25]
out: {"enu": [[e, n, u]...]} resampled every STEP m, and "track": the same in the scene's y-up frame (east, up, south)"""
import json, sys
import numpy as np
ref = np.array(json.load(open(sys.argv[1]))["enu"], float); walk = np.array(json.load(open(sys.argv[2]))["enu"], float)
ra, rb = map(int, sys.argv[4].split(":")); wa, wb = map(int, sys.argv[5].split(":")); step = float(sys.argv[6]) if len(sys.argv) > 6 else 0.25
seg = walk[wa:wb]
if np.linalg.norm(seg[0, :2] - ref[ra, :2]) > np.linalg.norm(seg[-1, :2] - ref[ra, :2]): seg = seg[::-1]   # the walk may run the other way
line = np.vstack([ref[:ra], seg, ref[rb:]])
print(f"joins: {np.linalg.norm(ref[ra - 1, :2] - seg[0, :2]):.2f} m and {np.linalg.norm(seg[-1, :2] - ref[rb, :2]):.2f} m")
# heights from the walk
d = np.linalg.norm(line[:, None, :2] - walk[None, :, :2], axis=2); near = d.argmin(1)
line[:, 2] = walk[near, 2]
print(f"nearest walk position: median {np.median(d.min(1)):.2f} m, max {d.min(1).max():.2f} m")
# resample evenly along the line, light smoothing of the heights
s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(line[:, :2], axis=0), axis=1))]
t = np.arange(0, s[-1], step); out = np.c_[[np.interp(t, s, line[:, k]) for k in range(3)]].T
k = int(5 / step); out[:, 2] = np.convolve(np.pad(out[:, 2], k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), "valid")
track = np.c_[out[:, 0], out[:, 2], -out[:, 1]]
json.dump(dict(enu=out.round(3).tolist(), track=track.round(3).tolist(), length=round(float(s[-1]), 1),
               note=f"reference flight with [{ra}:{rb}] from the walk [{wa}:{wb}]"), open(sys.argv[3], "w"))
print(f"{len(out)} points, {s[-1]:.0f} m -> {sys.argv[3]}")
