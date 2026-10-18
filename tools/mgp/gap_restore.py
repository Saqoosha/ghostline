"""Undo cpr_gap.py's drops outside the final row. Before the fix in cpr_gap.py it dropped every unsolved "jump" (a frame
faster than JUMP_MPS from both neighbours) anywhere on the course, but solves gaps only in the row: on the straight before
the start gate (~44 m/s) good frames went and took laps with them. A frame cpr_gap.py marked "how": "jump" whose pose in
the input loc json lies outside the row box (CBOX, track frame) gets that pose back.
usage: gap_restore.py <top_frame.json> <fit.json> <gap dir> <input loc dir>     (rewrites the gap dir's jsons in place)"""
import json, os, sys
import numpy as np

frame_json, fit_json, gap_dir, loc_dir = sys.argv[1:5]
F, Tf = json.load(open(frame_json)), json.load(open(fit_json))
E, a0 = np.array([F["ex"], F["ey"], F["up"]]), np.array(F["origin"]); R2 = np.array(Tf["R"]) @ np.diag([1, Tf["mirror"]])
CBOX = (-131, -104, -8, 22)


def track(X):
    G = (np.atleast_2d(X) - a0) @ E.T * float(Tf["scale"])
    return G[:, :2] @ R2.T + Tf["t"]


n_back = 0
for fn in sorted(os.listdir(gap_dir)):
    if not fn.endswith(".json") or not os.path.exists(f"{loc_dir}/{fn}"):
        continue
    G = json.load(open(f"{gap_dir}/{fn}")); L = {f["t_ms"]: f for f in json.load(open(f"{loc_dir}/{fn}"))["frames"]}
    back = 0
    for i, f in enumerate(G["frames"]):
        o = L.get(f["t_ms"])
        if f.get("how") == "jump" and o is not None and o.get("ok"):
            x, y = track(np.array(o["center"]))[0]
            if not (CBOX[0] < x < CBOX[1] and CBOX[2] < y < CBOX[3]):
                G["frames"][i] = o; back += 1
    if back:
        json.dump(G, open(f"{gap_dir}/{fn}", "w")); n_back += back
print(f"{gap_dir}: {n_back} frames back")
