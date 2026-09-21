"""refined.jsonl (per-frame photometric refinement) -> poses60_refined.json for the viewer.
Accepted frames are taken as they are; every other frame is interpolated (position: cubic
Hermite on the accepted frames; orientation: slerp), so the file has every frame index."""
import json, sys, numpy as np
from scipy.spatial.transform import Rotation as Rot, Slerp
MAXGAP = 0.5                                                   # s
init = json.load(open(sys.argv[1])); N, T0, FPS = len(init["poses"]), init["t0"], init["fps"]
rec = [json.loads(l) for l in open(sys.argv[2])]
acc = sorted([r for r in rec if r["accepted"]], key=lambda r: r["i"])
byi = {r["i"]: r for r in rec}
t = np.array([r["t"] for r in acc]); P = np.array([r["pos"] for r in acc]); sl = Slerp(t, Rot.from_quat([r["quat"] for r in acc]))
V = np.gradient(P, t, axis=0)
out = []
for i in range(N):
    p0 = init["poses"][i]
    if p0 is None: out.append(None); continue
    tt = T0 + i / FPS
    if p0["src"] == "ground": out.append({**p0}); continue        # one pose for the frames on the ground
    if i in byi and byi[i]["accepted"]:
        r = byi[i]; out.append({"i": i, "t": r["t"], "pos": r["pos"], "quat": r["quat"], "src": "refined"}); continue
    if p0["src"] in ("kept", "lk") or tt < t[0] or tt > t[-1]: out.append({**p0, "src": p0["src"]}); continue   # an observed frame keeps its pose
    k = min(int(np.searchsorted(t, tt, side="right") - 1), len(t) - 2); h = t[k+1] - t[k]; u = (tt - t[k]) / h
    # Hermite only across a short gap: between accepted frames 1 s apart it drew a 4 m bulge
    # (#500-#540, 2026-09-21) where the Kalman track was fine. Beyond MAXGAP the init pose stays.
    if h > MAXGAP: out.append({**p0, "src": p0["src"]}); continue
    h00, h10, h01, h11 = 2*u**3-3*u**2+1, u**3-2*u**2+u, -2*u**3+3*u**2, u**3-u**2
    pos = h00*P[k] + h10*h*V[k] + h01*P[k+1] + h11*h*V[k+1]
    out.append({"i": i, "t": round(tt, 4), "pos": np.round(pos, 3).tolist(), "quat": np.round(sl([tt]).as_quat()[0], 6).tolist(), "src": "interp"})
json.dump({**{k: v for k, v in init.items() if k != "poses"}, "poses": out}, open(sys.argv[3], "w"))
n_ref = sum(1 for o in out if o and o["src"] == "refined"); n_all = sum(1 for o in out if o)
sh = np.array([r["shift_m"] for r in acc]); rd = np.array([r["rot_deg"] for r in acc])
print(f"{n_all} frames: {n_ref} refined, {n_all-n_ref} interpolated | accepted {len(acc)}/{len(rec)} | shift p50 {np.median(sh):.2f} m p90 {np.percentile(sh,90):.2f} | rot p50 {np.median(rd):.1f} deg p90 {np.percentile(rd,90):.1f}")
