"""MGP-style localization (localize.py on an SfM map of the day's DVR frames) fused with the live answers, per heat, into the
relay's heat format (live.json: answers [t ms, x, y, z, qx, qy, qz, qw, inliers] per cell) for night_heats.py --relay.
The SfM map goes into the scan's frame by a similarity fitted to the live answers with MIN inliers or more at the same
instants (trimmed). A live answer is kept where it lies within NEAR m of the line through the localized poses around it
(within GAP s on both sides), or where no localized pose is that close in time; a localized pose always is.
  python mgp_fuse.py <model dir> <loc dir> <relay dir> <out relay dir> <heat id>...   (prints coverage, line distance, jumps)
JDL R7 2026-10-25 (rain): tracker answers 37-79% of a 15 fps grid with p90 10-25 m off the course line on the Pro final."""
import json, os, sys, numpy as np, pycolmap as p

MIN, TRIM, NEAR, GAP = 150, 3.0, 3.0, 1.0
JST_DAY = "20261025"


def quat_xyzw(R):                                     # rotation matrix -> (x, y, z, w)
    w = np.sqrt(max(0.0, 1 + R[0, 0] + R[1, 1] + R[2, 2])) / 2; x = np.sqrt(max(0.0, 1 + R[0, 0] - R[1, 1] - R[2, 2])) / 2
    y = np.sqrt(max(0.0, 1 - R[0, 0] + R[1, 1] - R[2, 2])) / 2; z = np.sqrt(max(0.0, 1 - R[0, 0] - R[1, 1] + R[2, 2])) / 2
    x, y, z = np.copysign(x, R[2, 1] - R[1, 2]), np.copysign(y, R[0, 2] - R[2, 0]), np.copysign(z, R[1, 0] - R[0, 1])
    return [float(x), float(y), float(z), float(w)]


def qmat_wxyz(q):                                     # COLMAP qvec (w, x, y, z) -> matrix
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def umeyama(X, Y):
    mx, my = X.mean(0), Y.mean(0); Xc, Yc = X - mx, Y - my; U, S, Vt = np.linalg.svd(Yc.T @ Xc / len(X))
    D = np.eye(3); D[2, 2] = np.sign(np.linalg.det(U @ Vt)); R = U @ D @ Vt; s = (S * np.diag(D)).sum() / (Xc ** 2).sum(1).mean()
    return s, R, my - s * R @ mx


def align(model, relay, heats):
    A, B = [], []
    for hid in heats:
        L = json.load(open(f"{relay}/{JST_DAY}/{hid}.json")); start = json.load(open(race_json(hid)))["start"]
        for im in model.images.values():
            folder, fn = im.name.split("/")
            if not folder.startswith(hid + "_"): continue
            c = folder[len(hid) + 1:]; rows = np.array(L["answers"].get(c, []))
            if not len(rows): continue
            t = start + int(fn[:-4]); k = np.argmin(np.abs(rows[:, 0] - t))
            if abs(rows[k, 0] - t) <= 20 and rows[k, 8] >= MIN: A.append(im.projection_center()); B.append(rows[k, 1:4])
    A, B = np.array(A), np.array(B); keep = np.ones(len(A), bool)
    for it in range(6):
        s, R, t = umeyama(A[keep], B[keep]); res = np.linalg.norm((s * (R @ A.T)).T + t - B, axis=1)
        keep = res < max(TRIM, np.percentile(res, 60)) if it < 3 else res < TRIM
    print(f"align: {len(A)} pairs, {keep.sum()} kept, scale {s:.3f}, residual p50 {np.median(res[keep]):.2f} p90 {np.percentile(res[keep], 90):.2f} m", flush=True)
    return s, R, t


race_json = lambda hid: f"/home/saqoosha/rt/night1025/out/{hid}/race.json"


def drop_jumps(rows, win=400, far=3.0):              # a localized pose off the straight line through its neighbours in time (a drone does
    for _ in range(2):                                 # 30 m/s, so their median is no reference; MGP poses_from_loc.py dropped such jumps)
        if len(rows) < 5: return rows
        t = rows[:, 0] / 1000; keep = np.ones(len(rows), bool)
        for k in range(len(rows)):
            m = np.abs(t - t[k]) <= win / 1000; m[k] = False
            if m.sum() < 4: continue
            c = np.polyfit(t[m] - t[k], rows[m, 1:4], 1)
            if np.linalg.norm(rows[k, 1:4] - c[1]) > far: keep[k] = False
        rows = rows[keep]
    return rows


def moving(rows, win=500, vmin=2.0):                 # localized poses only in flight: a still picture on the pads lands somewhere
    if len(rows) < 3: return rows                       # on the course (the map has the pads barely), 2.8 m off the line, not on them
    t = rows[:, 0] / 1000; keep = np.zeros(len(rows), bool)
    for k in range(len(rows)):
        m = np.abs(t - t[k]) <= win / 1000
        if m.sum() >= 3 and np.ptp(t[m]) > 0: keep[k] = np.linalg.norm(rows[m][-1, 1:4] - rows[m][0, 1:4]) / np.ptp(t[m]) > vmin
    return rows[keep]


def fuse(loc_rows, live_rows):
    """loc_rows, live_rows: [t ms, x, y, z, qx, qy, qz, qw, inl] sorted; returns the fused rows sorted"""
    if not len(loc_rows): return live_rows
    lt = loc_rows[:, 0]; out = [list(r) for r in loc_rows]
    for r in live_rows:
        k = np.searchsorted(lt, r[0]); a, b = k - 1, k
        near_a = a >= 0 and r[0] - lt[a] <= GAP * 1000; near_b = b < len(lt) and lt[b] - r[0] <= GAP * 1000
        if near_a and near_b:
            u = (r[0] - lt[a]) / max(1.0, lt[b] - lt[a]); ref = loc_rows[a, 1:4] * (1 - u) + loc_rows[b, 1:4] * u
            if np.linalg.norm(r[1:4] - ref) <= NEAR: out.append(list(r))
        elif near_a or near_b:
            ref = loc_rows[a if near_a else b, 1:4]
            if np.linalg.norm(r[1:4] - ref) <= NEAR + 10 * GAP: out.append(list(r))   # one side only: as far as a drone gets in GAP at 10 m/s more
        else: out.append(list(r))
    return np.array(sorted(out, key=lambda x: x[0]))


def stats(rows, line, w0, w1, rate=15):
    m = (rows[:, 0] >= w0) & (rows[:, 0] < w1); r = rows[m]; N = int(round((w1 - w0) / 1000 * rate))
    if not len(r): return "  0.0%"
    cov = len(np.unique(np.floor((r[:, 0] - w0) / 1000 * rate))) / N
    D = np.min(np.linalg.norm(r[:, None, [1, 3]] - line[None, ::2][:, :, [0, 2]], axis=2), axis=1)
    dt = np.diff(r[:, 0]) / 1000; dd = np.linalg.norm(np.diff(r[:, 1:4], axis=0), axis=1)
    return f"{100 * cov:5.1f}%  line p50 {np.median(D):5.2f} p90 {np.percentile(D, 90):5.2f} m  jumps>15m {int(((dd > 15) & (dd / np.maximum(dt, 1e-3) > 35)).sum()):3d}"


def main():
    model_dir, loc, relay, out, heats = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]
    # ALIGN=<json>: the similarity solved once on the base map; a model registered onto it with its frames fixed shares it
    model = p.Reconstruction(model_dir)
    if os.environ.get("ALIGN"): a = json.load(open(os.environ["ALIGN"])); s, R, t = a["s"], np.array(a["R"]), np.array(a["t"])
    else:
        s, R, t = align(model, relay, heats)
        if os.environ.get("SAVE_ALIGN"): json.dump(dict(s=float(s), R=R.tolist(), t=t.tolist()), open(os.environ["SAVE_ALIGN"], "w"))
    line = np.array(json.load(open("/home/saqoosha/rt/tracks/jdl-2026-r7-v1.json"))["track"]); os.makedirs(f"{out}/{JST_DAY}", exist_ok=True)
    for hid in heats:
        L = json.load(open(f"{relay}/{JST_DAY}/{hid}.json")); start = json.load(open(race_json(hid)))["start"]; new = dict(L, answers={})
        for c in L["order"]:
            live = np.array(sorted(L["answers"].get(c, []), key=lambda x: x[0])); f = f"{loc}/{hid}_{c}.json"
            if not os.path.exists(f): new["answers"][c] = live.tolist(); continue
            rows = []
            for fr in json.load(open(f))["frames"]:
                if not fr.get("ok"): continue
                Rcw = qmat_wxyz(fr["qvec"]); Rwc = R @ Rcw.T; pos = s * (R @ np.array(fr["center"])) + t
                rows.append([start + fr["t_ms"], *pos.tolist(), *quat_xyzw(Rwc), fr.get("inliers", 0)])
            loc_rows = moving(drop_jumps(np.array(sorted(rows, key=lambda x: x[0])))) if rows else np.zeros((0, 9))
            fused = fuse(loc_rows, live) if len(live) else loc_rows; new["answers"][c] = [[round(v, 4) if k else int(v) for k, v in enumerate(r)] for r in fused.tolist()]
            if len(loc_rows):
                w0, w1 = loc_rows[0, 0], loc_rows[-1, 0]
                print(f"{hid} {c} {L['cells'].get(c, ''):16s} live  {stats(live, line, w0, w1) if len(live) else '-'}\n{'':28s} loc   {stats(loc_rows, line, w0, w1)}\n{'':28s} fused {stats(fused, line, w0, w1)}", flush=True)
        json.dump(new, open(f"{out}/{JST_DAY}/{hid}.json", "w"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__": main()
