"""Unsolve the frames whose view barely changes in half a second, after the quad first moves. In the 4-up recordings a
finish card (photo, place, time) is drawn over each pilot's feed after the finish; the night map registered those
frames at one spot (page world x -123.8, z -6.5), so every finisher jumped there after the race. A parked quad's frames
go too: its last moving frame stays as its place. Frames before take-off stay (the quad on its pad, rightly placed).
A frame is still when more than STATIC (0.3) of its unmasked pixels are within 5 levels of the frame 0.5 s later
(0.5 s earlier in the recording's last half second, where the black end screen sat at the map's spot for the cards).
usage (rt4090, in night/): drop_static.py <loc dir> <images root> <masks root> <out dir> <feed> ..."""
import json, os, sys
import cv2, numpy as np

src, iroot, mroot, out = sys.argv[1:5]; feeds = sys.argv[5:]
THR = float(os.environ.get("STATIC", 0.3))
U_M, FROZEN_M, FROZEN_N = 7.367, float(os.environ.get("FROZEN_M", 0.1)), int(os.environ.get("FROZEN_N", 6))   # metres per map unit (the day map)
os.makedirs(out, exist_ok=True)
card = None
if os.environ.get("FRAME"):                                  # FRAME / FIT: to_metric's top_frame.json and fit json
    Fr, Tf = json.load(open(os.environ["FRAME"])), json.load(open(os.environ["FIT"]))
    E, a0 = np.array([Fr["ex"], Fr["ey"], Fr["up"]]), np.array(Fr["origin"]); R2 = np.array(Tf["R"]) @ np.diag([1, Tf["mirror"]])
    track = lambda X: ((np.asarray(X) - a0) @ E.T * float(Tf["scale"]))[:2] @ R2.T + Tf["t"]
    card = np.array([float(v) for v in os.environ.get("CARD", "-123.8,6.5,1.0").split(",")])
look = lambda p: cv2.GaussianBlur(cv2.imread(p, cv2.IMREAD_GRAYSCALE), (5, 5), 0).astype(np.int16)
for feed in feeds:
    J = json.load(open(f"{src}/{feed}.json"))
    ts = sorted(int(f[:-4]) for f in os.listdir(f"{iroot}/{feed}") if f.endswith(".jpg") and f[:-4].isdigit())
    still = {}
    for t in ts:
        k = np.searchsorted(ts, t + 450)
        if k == len(ts) or ts[k] > t + 1500:                 # the recording's last half second: the frame before instead
            k = np.searchsorted(ts, t - 450, side="right") - 1
            if k < 0 or ts[k] < t - 1500: continue
        m = cv2.imread(f"{mroot}/{feed}/{t:07d}.jpg.png", cv2.IMREAD_GRAYSCALE)
        d = np.abs(look(f"{iroot}/{feed}/{t:07d}.jpg") - look(f"{iroot}/{feed}/{ts[k]:07d}.jpg"))
        still[t] = float(((d[m > 0] if m is not None else d) < 5).mean()) > THR
    moved = next((t for t in ts if t in still and not still[t]), None)   # take-off: the first frame that changes
    n = 0
    for f in J["frames"]:
        if f["ok"] and moved is not None and f["t_ms"] > moved and still.get(f["t_ms"], False):
            f.update(ok=False, how="still"); n += 1
    # frozen: the same pose to FROZEN_M (0.1 m) over FROZEN_N (6) frames in a row after take-off. The night map matches the finish
    # card itself (about 1,000 points, the same place within 1-3 cm) even while the quad behind it still flies, so
    # the view changes and the still test passes; a real hover or a parked quad still wobbles by centimetres.
    # the card's spot: the night map registered the finish card at one place (track frame x -123.8, y 6.5, 3.4 m up);
    # unconfirmed night poses within CARD_R (1 m) of it go, sparse low-inlier ones included, which the run test misses
    if card is not None:
        for f in J["frames"]:
            if f["ok"] and f.get("how") == "night" and np.hypot(*(track(f["center"])[:2] - card[:2])) < card[2]:
                f.update(ok=False, how="card"); n += 1
    ok = [f for f in J["frames"] if f["ok"] and moved is not None and f["t_ms"] > moved]
    run = []
    for f in ok + [None]:
        if f is not None and run and np.linalg.norm(np.subtract(f["center"], run[0]["center"])) * U_M < FROZEN_M:
            run.append(f); continue
        if len(run) >= FROZEN_N:
            for g in run: g.update(ok=False, how="frozen"); n += 1
        run = [f] if f is not None else []
    json.dump(J, open(f"{out}/{feed}.json", "w"))
    print(f"{feed}: {n} still or frozen frames unsolved after take-off at {moved / 1000 if moved else None} s", flush=True)
