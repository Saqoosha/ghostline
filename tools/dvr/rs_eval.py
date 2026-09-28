"""Residual of a flight's matches at its solved poses, with a rolling-shutter readout, binned by turn rate.
usage: rs_eval.py <flight dir> <poses.json> <dump dir> <readout s>
The same readout model as cpr_ba.py (RS_READOUT): row v is read readout * (v - cy) / (2 cy) after the middle row."""
import json, sys, glob, os, numpy as np
from scipy.spatial.transform import Rotation as Rot
D, poses, dump, tau = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
cam = json.load(open(f"{D}/dvr_pinhole.mp4.json")); fx, fy, cx, cy = cam["fx"], cam["fy"], cam["cx"], cam["cy"]
J = json.load(open(f"{D}/{poses}")); P = J["poses"]; dt = 1 / J["fps"]; rows = []
for f in sorted(glob.glob(f"{D}/{dump}/*.npz")):
    i = int(os.path.basename(f)[:5])
    if not (0 < i < len(P) - 1) or not all(P[j] for j in (i - 1, i, i + 1)) or P[i]["src"] != "ba": continue
    Rm, R0, Rp = (Rot.from_quat(P[j]["quat"]) for j in (i - 1, i, i + 1))
    om = ((Rm.inv() * R0).as_rotvec() + (R0.inv() * Rp).as_rotvec()) / (2 * dt)
    vel = (np.array(P[i + 1]["pos"]) - np.array(P[i - 1]["pos"])) / (2 * dt)
    z = np.load(f); X, uv = z["X"].astype(float), z["uv"].astype(float)
    if "inl" in z.files: X, uv = X[z["inl"]], uv[z["inl"]]
    s = tau * (uv[:, 1] - cy) / (2 * cy)
    d = Rot.from_rotvec(om[None] * s[:, None]).inv().apply((X - np.array(P[i]["pos"]) - vel[None] * s[:, None]) @ R0.as_matrix())
    ok = d[:, 2] > 0.1; u = np.c_[fx * d[ok, 0] / d[ok, 2] + cx, fy * d[ok, 1] / d[ok, 2] + cy]
    rows.append((np.degrees(np.linalg.norm(om)), np.median(np.linalg.norm(u - uv[ok], axis=1))))
a = np.array(rows)
out = [f"all {np.median(a[:,1]):.2f}"]
for lo, hi in [(0, 150), (150, 300), (300, 500), (500, 3000)]:
    m = (a[:, 0] >= lo) & (a[:, 0] < hi)
    if m.sum() > 5: out.append(f"{lo}-{hi}: {np.median(a[m,1]):.2f}")
print(f"readout {tau*1000:+5.1f} ms, {len(a)} frames, residual p50 px: " + " | ".join(out))
