"""localize.py output (SfM map frame) -> make_mgp_race.py pose files (world metres, y up, x = VelociDrone x,
z = -VelociDrone z). Chain: map -> ground frame (topview.py *_frame.json) -> 2D similarity onto the track (fit2d.py
.json). Drops excursions where the map holds a piece of course twice: a jump of more than JUMP m (beyond VMAX m/s over
the time between the frames) that comes back (a JUMP m step) within BACK s; a jump that never comes back is kept.
usage: poses_from_loc.py <frame.json> <fit.json> <out_dir> <loc.json>=<PILOT>[:slot] ... [--dt-ms=N] [--smooth]
--dt-ms shifts every frame time (frames named by an older extract.py are 0.5/fps early: pass +500/fps)."""
import json, os, sys
import numpy as np

JUMP, BACK, VMAX = 15.0, 3.0, 50.0
args = [a for a in sys.argv[1:] if not a.startswith("--")]
dt = next((float(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--dt-ms=")), 0.0)
F, T = json.load(open(args[0])), json.load(open(args[1]))
out = args[2]
E = np.array([F["ex"], F["ey"], F["up"]]); a0 = np.array(F["origin"]); s = T["scale"]
H = np.array(T["R"]) @ np.diag([1, T["mirror"]]) @ E[:2]                 # map -> VelociDrone [x, z] (unscaled)
M = np.array([H[0], E[2], -H[1]])                                        # map -> world axes; proper rotation
assert np.linalg.det(M) > 0
tvd = np.array(T["t"])


def quat_xyzw(R):
    t = np.trace(R)
    if t > 0:
        k = np.sqrt(t + 1) * 2; return [(R[2, 1] - R[1, 2]) / k, (R[0, 2] - R[2, 0]) / k, (R[1, 0] - R[0, 1]) / k, k / 4]
    i = int(np.argmax(np.diag(R))); j, l = (i + 1) % 3, (i + 2) % 3
    k = np.sqrt(R[i, i] - R[j, j] - R[l, l] + 1) * 2; q = np.zeros(4)
    q[i] = k / 4; q[j] = (R[j, i] + R[i, j]) / k; q[l] = (R[l, i] + R[i, l]) / k; q[3] = (R[l, j] - R[j, l]) / k
    return q.tolist()


def rot_wxyz(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


# The TEAM BLACKSHEEP gate in the final row is two identical frames stacked (the same print on both bars): a frame
# passing under it (course pass 4: after the pass under NEROS, before the low gate) is often localized one storey up
# (Round 44, MCKFPV, 50.97-51.10 s: 2.9-3.8 m high while going through the lower opening; 51.23 s, past it, 1.65 m).
# Such frames come down by STOREY (the height between the two prints). Page world frame: x, up, -VD z.
TBS_ZONE = (-126.5, -121.0, -10.0, -4.0)            # x0, x1, z0, z1 around the TBS gate
NEROS_LOW = (-116.5, -110.0, -10.0, -2.0, 1.6)      # x0, x1, z0, z1, highest: under NEROS (as unfolded, mapfix_unfold.py)
LOW_GATE = (-126.5, -9.0, -5.0)                     # x below, z0, z1: at the low gate, the end of the stretch
STOREY, STOREY_TOP, STOREY_WINDOW = 2.3, 2.0, 4.0   # m, m (above the upper opening's base), s


def storey_fix(t, C):
    under_neros = np.nonzero((C[:, 0] > NEROS_LOW[0]) & (C[:, 0] < NEROS_LOW[1]) & (C[:, 2] > NEROS_LOW[2])
                             & (C[:, 2] < NEROS_LOW[3]) & (C[:, 1] < NEROS_LOW[4]))[0]
    at_low_gate = (C[:, 0] < LOW_GATE[0]) & (C[:, 2] > LOW_GATE[1]) & (C[:, 2] < LOW_GATE[2])
    tbs_top = (C[:, 0] > TBS_ZONE[0]) & (C[:, 0] < TBS_ZONE[1]) & (C[:, 2] > TBS_ZONE[2]) & (C[:, 2] < TBS_ZONE[3]) & (C[:, 1] > STOREY_TOP)
    moved = np.zeros(len(C), bool)
    for i in under_neros:
        j = i + 1
        while j < len(C) and t[j] - t[i] <= STOREY_WINDOW * 1000 and not at_low_gate[j]:
            if tbs_top[j] and not moved[j]:
                C[j, 1] -= STOREY; moved[j] = True
            j += 1
    return int(moved.sum())


SMOOTH = "--smooth" in sys.argv
LAM, DELTA, REJECT = float(os.environ.get("LAM", 2e-4)), 0.3, 2.0


def smooth(ts, P):
    """Robust jerk-penalised fit on a 60 fps grid: data rows interpolate the grid at each observation time, a third
    difference penalty keeps jerk low, Huber IRLS (delta 0.3 m) downweights outliers and residuals over 2 m are dropped.
    Gaps get a smooth curve instead of a straight line. LAM sets the jerk penalty (per grid step^3)."""
    from scipy.sparse import csr_matrix, diags, vstack
    from scipy.sparse.linalg import spsolve
    T = np.arange(ts[0], ts[-1] + 1e-9, 1 / 60); n = len(T)
    u = (ts - T[0]) * 60; i0 = np.minimum(u.astype(int), n - 2); f = u - i0
    A = csr_matrix((np.r_[1 - f, f], (np.r_[np.arange(len(ts)), np.arange(len(ts))], np.r_[i0, i0 + 1])), shape=(len(ts), n))
    D = diags([-1.0, 3.0, -3.0, 1.0], [0, 1, 2, 3], shape=(n - 3, n))
    w = np.ones(len(ts))
    for _ in range(6):
        W = diags(w); X = spsolve((A.T @ W @ A + LAM ** -1 * (D.T @ D)).tocsc(), A.T @ W @ P)
        r = np.linalg.norm(A @ X - P, axis=1)
        w = np.where(r > REJECT, 0, np.minimum(1, DELTA / np.maximum(r, 1e-9)))
    return T, X, (w == 0).sum()


os.makedirs(out, exist_ok=True)
for spec in args[3:]:
    path, who = spec.split("=")
    name, slot = (who.split(":") + [None])[:2]
    L = [f for f in json.load(open(path))["frames"] if f["ok"]]
    if len(L) < 2:                                       # a cell that never localized (noise, analog): skip it, not the rest
        print(f"{name}: {len(L)} localized frames, skipped"); continue
    t = np.array([f["t_ms"] for f in L], float) + dt
    C = (np.array([f["center"] for f in L]) - a0) @ M.T * s
    C[:, [0, 2]] += [tvd[0], -tvd[1]]
    down = storey_fix(t, C)
    if down:
        print(f"  {name}: {down} frames under the TBS gate brought down one storey")
    keep = np.ones(len(C), bool)
    i = 1
    # leaving: a jump of JUMP m more than a quad could fly in the time between the two frames (VMAX). Across a gap of
    # missing frames a plain 15 m test took a 2 s, 35 m stretch of the start straight for an excursion and dropped 3 s
    # with a lap in them (Round 61 MINJAE_). Coming back stays a plain JUMP m step: with the allowance a 16.1 m return
    # in 66 ms was missed and 15 good frames after a 3-frame excursion went too (Round 54 NIDO_FPV)
    leaves = lambda a, b: np.linalg.norm(C[b] - C[a]) > JUMP + VMAX * (t[b] - t[a]) / 1000
    while i < len(C):
        if leaves(i - 1, i):
            back = next((j for j in range(i + 1, len(C)) if t[j] - t[i] <= BACK * 1000
                         and np.linalg.norm(C[j] - C[i - 1]) < JUMP + 30 * (t[j] - t[i - 1]) / 1000
                         and np.linalg.norm(C[j] - C[j - 1]) > JUMP), None)
            if back is not None:
                keep[i:back] = False; i = back
        i += 1
    kk = np.nonzero(keep)[0]
    Q = [quat_xyzw(M @ rot_wxyz(L[k]["qvec"]).T) for k in kk]             # camera -> world
    if SMOOTH:
        # a gap longer than GAP s (lost tracking, or the landed quad's sky frames) is not bridged: each run is
        # smoothed on its own and runs shorter than MIN_RUN frames (a stray registration) are dropped. Bridging a
        # 27 s gap with one jerk-penalised curve once swung a path 700 m out of the field
        GAP, MIN_RUN = 1.5, 10
        runs = np.split(np.arange(len(kk)), np.nonzero(np.diff(t[kk]) > GAP * 1000)[0] + 1)
        frames, rej, drop = [], 0, 0
        for r in runs:
            if len(r) < MIN_RUN:
                drop += len(r); continue
            T60, X, out_w = smooth(t[kk[r]] / 1000, C[kk[r]]); rej += int(out_w)
            near = r[np.clip(np.searchsorted(t[kk[r]] / 1000, T60), 0, len(r) - 1)]
            frames += [{"t_ms": int(round(T60[i] * 1000)), "center": np.round(X[i], 3).tolist(), "qvec": np.round(Q[near[i]], 5).tolist()}
                       for i in range(len(T60))]
        print(f"  smoothed to {len(frames)} frames at 60 fps in {len(runs)} runs; {rej} observations rejected as outliers, {drop} in short runs dropped")
    else:
        frames = [{"t_ms": int(round(t[k])), "center": np.round(C[k], 3).tolist(), "qvec": np.round(q, 5).tolist()} for k, q in zip(kk, Q)]
    json.dump({"pilot": name, "slot": int(slot) if slot else None, "qvec_source": "localize.py", "frames": frames},
              open(f"{out}/{name}.json", "w"))
    print(name, len(L), "frames,", int((~keep).sum()), "dropped as duplicate-map excursions")
