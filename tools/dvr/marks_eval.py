"""How far does each human mark sit from where the pose set projects its landmark? The human's
truth against any pose set, in pixels. usage: marks_eval.py marks.json dvr_pinhole.mp4.json poses.json [poses2.json ...]"""
import json, sys, numpy as np
from scipy.spatial.transform import Rotation as Rot
M = json.load(open(sys.argv[1])); cam = json.load(open(sys.argv[2])); K = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
def project(p, X):
    d = Rot.from_quat(p["quat"]).as_matrix().T @ (np.array(X) - np.array(p["pos"]))
    return None if d[2] < 0.1 else np.array([K[0, 0] * d[0] / d[2] + K[0, 2], K[1, 1] * d[1] / d[2] + K[1, 2]])
for path in sys.argv[3:]:
    P = {p["i"]: p for p in json.load(open(path))["poses"] if p}; res = []; behind = 0
    for m in M["marks"]:
        X = M["landmarks"].get(m["id"], {}).get("pos")
        if not X or m["i"] not in P: continue
        uv = project(P[m["i"]], X)
        if uv is None: behind += 1; continue
        res.append(float(np.linalg.norm(uv - np.array([m["u"], m["v"]]))))
    r = np.array(res)
    print(f"{path.split('/')[-1]}: {len(r)} marks, residual p50 {np.median(r):.1f} px p90 {np.percentile(r, 90):.1f} max {r.max():.0f}; within 10 px {np.mean(r < 10)*100:.0f}%, within 30 px {np.mean(r < 30)*100:.0f}%; landmark behind camera {behind}")
