"""Keep the night poses that rendering confirms, and leave the folded part's wrong ones as gaps for cpr_gap.py.
night_refine.py's answers jitter more from frame to frame than the night map's poses (midpoint deviation p50 0.67 m
against 0.27 m on the A final, 0.62 m even with 1,500+ inliers), while the slow part of their difference is small
(p50 0.13 m, p90 0.42 m over a 1 s median). So a frame keeps its night pose, moved by that slow part, when the render
confirmed it, and also outside the folded part when it did not (a dark or blurred view there, not a wrong place). A
frame in the folded part whose render did not match is left ok: false: the map put it on the other arc, and
cpr_gap.py (ALL=1) tracks through it from both sides.
usage: night_keep.py <night_refine.py out dir> <top_frame.json> <fit.json> <out dir> <feed> ...
env: FOLD (page world x0,x1,z0,z1: -138,-104,-60,-22), WIN (1.0 s: half the window of the slow part)"""
import json, os, sys
import numpy as np

src, frame_json, fit_json, out = sys.argv[1:5]; feeds = sys.argv[5:]
x0, x1, z0, z1 = [float(v) for v in os.environ.get("FOLD", "-138,-104,-60,-22").split(",")]
WIN = float(os.environ.get("WIN", 1.0))
F, Tf = json.load(open(frame_json)), json.load(open(fit_json))
E, a0 = np.array([F["ex"], F["ey"], F["up"]]), np.array(F["origin"]); R2 = np.array(Tf["R"]) @ np.diag([1, Tf["mirror"]]); U = float(Tf["scale"])
def track(X):                                         # map -> track frame (x = VD x, y = VD z), metres
    G = (np.atleast_2d(X) - a0) @ E.T * U
    return G[:, :2] @ R2.T + Tf["t"]
os.makedirs(out, exist_ok=True)
for feed in feeds:
    J = json.load(open(f"{src}/{feed}.json")); fr = J["frames"]
    t = np.array([f["t_ms"] for f in fr]) / 1000
    night = np.array(["night" in f for f in fr]); conf = np.array([f["ok"] for f in fr])
    N = np.array([f["night"]["center"] if "night" in f else [np.nan] * 3 for f in fr])
    D = np.array([np.subtract(f["center"], f["night"]["center"]) if f["ok"] else [np.nan] * 3 for f in fr])
    g = track(np.nan_to_num(N)); fold = night & (x0 < g[:, 0]) & (g[:, 0] < x1) & (z0 < -g[:, 1]) & (-g[:, 1] < z1)
    tc = t[conf]
    frames, n_keep, n_gap = [], 0, 0
    for i, f in enumerate(fr):
        if night[i] and (conf[i] or not fold[i]):
            near = conf & (np.abs(t - t[i]) <= WIN)
            if not near.any(): near = conf & (np.abs(t - t[i]) <= 3 * WIN)
            slow = np.median(D[near], axis=0) if near.any() else np.zeros(3)
            frames.append({"t_ms": f["t_ms"], "ok": True, "inliers": f["inliers"] if conf[i] else f["night"]["inliers"],
                           "center": np.round(N[i] + slow, 5).tolist(), "qvec": f["night"]["qvec"], "how": "night" + ("+render" if conf[i] else "")})
            n_keep += 1
        else:
            frames.append({"t_ms": f["t_ms"], "ok": False, "inliers": 0, "how": "fold" if fold[i] else f.get("how", "none")})
            n_gap += fold[i]
    J["frames"] = frames; J["night_keep"] = {"kept": n_keep, "fold_gaps": int(n_gap), "confirmed": int(conf.sum())}
    json.dump(J, open(f"{out}/{feed}.json", "w"))
    print(f"{feed}: {n_keep} night poses kept ({conf.sum()} confirmed by rendering), {n_gap} in the folded part left to track", flush=True)
