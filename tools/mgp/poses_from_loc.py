"""localize.py output (SfM map frame) -> make_mgp_race.py pose files (world metres, y up, x = VelociDrone x,
z = -VelociDrone z). Chain: map -> ground frame (topview.py *_frame.json) -> 2D similarity onto the track (fit2d.py
.json). Drops the map's mistakes (a piece of course held twice, a gate taken for its twin): short runs of frames that
an impossible step (faster than VIMP) cuts off from the rest, the shortest first.
usage: poses_from_loc.py <frame.json> <fit.json> <out_dir> <loc.json>=<PILOT>[:slot] ... [--dt-ms=N] [--smooth]
--dt-ms shifts every frame time (frames named by an older extract.py are 0.5/fps early: pass +500/fps)."""
import json, os, sys
import numpy as np

VIMP, GAP_RUN, MAX_EXC = 80.0, 0.5, 15     # m/s, s, frames
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
    # The map's mistakes (a piece of course held twice, a gate taken for its twin) show as stray frames and short runs
    # of frames between impossible steps (faster than VIMP over more than 4 m). After the single stray frames, runs are
    # cut at impossible steps and at gaps over GAP_RUN s; the shortest run (up to MAX_EXC frames) touching an
    # impossible step goes, if its neighbours then make a possible step (a tie goes to the drop that leaves the slower
    # bridge), until no impossible step is left. Distance-only tests both missed
    # returns (a 16.1 m step in 66 ms, Round 54) and took good stretches for excursions: 2 s of the start straight
    # after a gap (Round 61), the good frames after two stray ones (Round 82)
    keep = np.ones(len(C), bool)

    def speed(a, b):
        return np.linalg.norm(C[b] - C[a]) / max((t[b] - t[a]) / 1000, 1 / 60)

    def imposs(a, b):
        return np.linalg.norm(C[b] - C[a]) > 4 and speed(a, b) > VIMP

    # single stray frames first: an impossible step on one side, its neighbours a possible step without it (the
    # largest gain first). A stray frame whose other step stays under VIMP would otherwise join the run beside it and
    # leave the good run next to it to be dropped (Round 61: 60.90 s at 109 m/s in, 66 m/s out, and the 3 s before it)
    while True:
        idx = np.nonzero(keep)[0]; best = None
        for k in range(1, len(idx) - 1):
            p_, i_, n_ = idx[k - 1], idx[k], idx[k + 1]
            if (imposs(p_, i_) or imposs(i_, n_)) and not imposs(p_, n_):
                gain = max(speed(p_, i_), speed(i_, n_)) - speed(p_, n_)
                if best is None or gain > best[0]: best = (gain, i_)
        if best is None:
            break
        keep[best[1]] = False
    while True:
        idx = np.nonzero(keep)[0]
        st = [imposs(idx[k], idx[k + 1]) for k in range(len(idx) - 1)]
        if len(idx) < 3 or not any(st):
            break
        cuts = [k for k in range(len(idx) - 1) if st[k] or t[idx[k + 1]] - t[idx[k]] > GAP_RUN * 1000]
        bounds = [0] + [k + 1 for k in cuts] + [len(idx)]
        runs = list(zip(bounds[:-1], bounds[1:]))               # [start, end) positions in idx
        best = None
        for r, (a, b) in enumerate(runs):
            if b - a > MAX_EXC or not ((a > 0 and st[a - 1]) or (b < len(idx) and st[b - 1])):
                continue
            if 0 < a and b < len(idx):
                p_, n_ = idx[a - 1], idx[b]
                if imposs(p_, n_):
                    continue
                v = np.linalg.norm(C[n_] - C[p_]) / max((t[n_] - t[p_]) / 1000, 1 / 60)
            else:                                               # at an end: only if shorter than the run beyond the step
                o = runs[r + 1] if a == 0 else runs[r - 1]
                if b - a >= o[1] - o[0]:
                    continue
                v = 0.0
            if best is None or (b - a, v) < best[0]:
                best = ((b - a, v), a, b)
        if best is None:
            break
        keep[idx[best[1]:best[2]]] = False
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
    print(name, len(L), "frames,", int((~keep).sum()), "dropped as impossible jumps")
