"""List the frame files cpr_gap.py needs for some rounds: every frame within CONTEXT_MS of a frame localized near the
final row (CBOX, track frame), images and masks, as paths relative to the rounds' parent (for tar -T), so only those
go to the GPU machine (the rounds hold 62 GB of frames).
usage (from ~/mgp): gap_frames.py <top_frame.json> <fit.json> <round loc dir>... > list.txt"""
import json, os, sys
import numpy as np

F, T = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
E, a0 = np.array([F["ex"], F["ey"], F["up"]]), np.array(F["origin"]); R2 = np.array(T["R"]) @ np.diag([1, T["mirror"]])
CBOX = [float(v) for v in os.environ.get("CBOX", "-131,-104,-8,22").split(",")]
CONTEXT_MS = int(os.environ.get("CONTEXT_MS", 3000))


def metric(X):
    G = (np.atleast_2d(X) - a0) @ E.T * T["scale"]
    return np.c_[G[:, :2] @ R2.T + T["t"], G[:, 2]]


for ld in sys.argv[3:]:
    ld = ld.rstrip("/"); q = os.path.basename(ld).split("_")[0]; base = os.path.join(os.path.dirname(ld), q)
    for fn in sorted(os.listdir(ld)):
        if not fn.endswith(".json"):
            continue
        cell = fn[:-5]; J = json.load(open(f"{ld}/{fn}"))
        ok = [f for f in J["frames"] if f["ok"]]
        if not ok:
            continue
        M = metric(np.array([f["center"] for f in ok]))
        near = np.array([f["t_ms"] for f, m in zip(ok, M) if CBOX[0] < m[0] < CBOX[1] and CBOX[2] < m[1] < CBOX[3]])
        if not len(near):
            continue
        for f in J["frames"]:
            if np.abs(near - f["t_ms"]).min() <= CONTEXT_MS:
                name = "%07d.jpg" % f["t_ms"]
                for kind, ext in (("images", ""), ("masks", ".png")):
                    p = f"{base}/{kind}/{cell}/{name}{ext}"
                    if os.path.exists(p):
                        print(p)
