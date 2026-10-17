"""Similarity transform from today's SfM (arbitrary scale) to the old scan, from frames placed in both.
  sim_align.py <sparse dir> <out.json> <prefix>=<answers.jsonl> [...]
prefix is the SfM image folder (v132/cam0); answers are rt_track.py .jsonl rows (i, pos, inl) or cpr_track.py records
(i, pos, inliers). A frame pairs when <prefix>/<i:05d>.jpg is in the SfM. RANSAC over 3-point similarities, then Umeyama on
the inliers. out.json is {"scale", "R", "t"} for VDGS tools/fit_transform.py --apply: x_old = scale * R @ x_sfm + t."""
import sys, json, struct, numpy as np

sparse, out = sys.argv[1], sys.argv[2]
MIN_INL, THR = 60, 0.5                                   # answer inliers; RANSAC inlier distance in the old scan's metres

def images(path):                                        # COLMAP images.bin -> {name: camera centre}
    c = {}
    with open(path, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            _id, qw, qx, qy, qz, tx, ty, tz, _cam = struct.unpack("<idddddddi", f.read(64))
            name = b""
            while (ch := f.read(1)) != b"\0": name += ch
            npt, = struct.unpack("<Q", f.read(8)); f.seek(24 * npt, 1)
            q = np.array([qw, qx, qy, qz]); w, x, y, z = q / np.linalg.norm(q)
            R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                          [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                          [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
            c[name.decode()] = -R.T @ np.array([tx, ty, tz])
    return c

def umeyama(A, B):                                       # B ~ s R A + t
    ma, mb = A.mean(0), B.mean(0); a, b = A - ma, B - mb
    U, S, Vt = np.linalg.svd(b.T @ a / len(A)); D = np.eye(3); D[2, 2] = np.sign(np.linalg.det(U @ Vt))
    R = U @ D @ Vt; s = np.trace(np.diag(S) @ D) / (a ** 2).sum(1).mean()
    return s, R, mb - s * R @ ma

cams = images(f"{sparse}/images.bin"); A, B, tag = [], [], []
for arg in sys.argv[3:]:
    pre, path = arg.split("=", 1)
    rows = [json.loads(l) for l in open(path)] if path.endswith(".jsonl") else list(json.load(open(path)).values())
    n = 0
    for r in rows:
        if "pos" not in r or r.get("inl", r.get("inliers", 0)) < MIN_INL: continue
        k = f"{pre}/{int(r['i']):05d}.jpg"
        if k in cams: A.append(cams[k]); B.append(r["pos"]); tag.append(pre); n += 1
    print(f"{pre}: {n} frames in both")
A, B, tag = np.array(A), np.array(B), np.array(tag)
assert len(A) >= 10, "too few frames placed in both"
rng = np.random.default_rng(0); best = None
for _ in range(5000):
    idx = rng.choice(len(A), 3, replace=False)
    if np.linalg.matrix_rank(A[idx] - A[idx].mean(0), tol=1e-6) < 2: continue
    s, R, t = umeyama(A[idx], B[idx]); inl = np.linalg.norm((s * (R @ A.T)).T + t - B, axis=1) < THR
    if best is None or inl.sum() > best.sum(): best = inl
for _ in range(3):                                       # refit on the inliers, re-select
    s, R, t = umeyama(A[best], B[best]); e = np.linalg.norm((s * (R @ A.T)).T + t - B, axis=1); best = e < THR
s, R, t = umeyama(A[best], B[best]); e = np.linalg.norm((s * (R @ A.T)).T + t - B, axis=1)
print(f"scale {s:.4f} m per SfM unit; inliers {best.sum()}/{len(A)}; error p50 {np.median(e[best]):.3f} m, p90 {np.percentile(e[best], 90):.3f} m")
for p in np.unique(tag):
    m = (tag == p) & best
    if m.any(): print(f"  {p}: {m.sum()} inliers, p50 {np.median(e[m]):.3f} m")
json.dump(dict(scale=float(s), R=R.tolist(), t=t.tolist(), inliers=int(best.sum()), pairs=len(A)), open(out, "w"), indent=1)
