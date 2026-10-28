"""A smooth correction onto the day map for a map that is right only near where it grew from. The evening map
(night_sfm_hloc.py) matched the day map to 0.3 m around the start area under one similarity but drifted to 3.6 m and
then tens of metres towards the far end of the course. So every anchor (a frame both maps placed: the D final at noon)
gets its own similarity, fitted by RANSAC to its K nearest anchors (and kept only if it agrees with them itself); a
point takes the inverse-distance blend of its 4 nearest anchors' similarities for its position, the nearest one's
rotation and scale. Points and anchors are in day-map coordinates after the global similarity (night_anchor.py's
transform.json), so the same field moves camera centres (night_anchor.py LOCAL=1 fits it into <out dir>/field.npz)
and a 3DGS trained on the globally moved cameras.
usage: night_warp.py splat <field.npz> <in.ply> <out.ply>"""
import sys
import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation as Rot


def umeyama(A, B):
    """B ~ s R A + t (rows are points) -> s, R, t"""
    ma, mb = A.mean(0), B.mean(0); a, b = A - ma, B - mb
    U, S, Vt = np.linalg.svd(b.T @ a / len(A))
    D = np.eye(3); D[2, 2] = np.sign(np.linalg.det(U @ Vt))
    R = U @ D @ Vt; sc = (S * np.diag(D)).sum() / (a ** 2).sum(1).mean()
    return sc, R, mb - sc * R @ ma


def fit(P, Q, K=30, thr=0.11, iters=150, min_inl=8, seed=0):
    """anchors P (globally moved) -> Q (day map): a similarity per anchor that agrees with its neighbourhood"""
    rng = np.random.default_rng(seed); n = len(P)
    _, nb = cKDTree(P).query(P, k=min(K, n))
    S, Rs, T, ok = np.ones(n), np.tile(np.eye(3), (n, 1, 1)), np.zeros((n, 3)), np.zeros(n, bool)
    for i in range(n):
        a, b = P[nb[i]], Q[nb[i]]; best = None
        for _ in range(iters):
            k = rng.choice(len(a), 3, replace=False)
            try:
                s, R, t = umeyama(a[k], b[k])
            except np.linalg.LinAlgError:
                continue
            if not (np.isfinite(s) and s > 0):
                continue
            m = np.linalg.norm(s * a @ R.T + t - b, axis=1) < thr
            if best is None or m.sum() > best.sum(): best = m
        if best is None or best.sum() < min_inl:
            continue
        s, R, t = umeyama(a[best], b[best])
        if np.linalg.norm(s * R @ P[i] + t - Q[i]) > thr:              # the anchor itself disagrees: a wrong anchor
            continue
        S[i], Rs[i], T[i], ok[i] = s, R, t, True
    return dict(P=P[ok], s=S[ok], R=Rs[ok], t=T[ok])


class Field:
    def __init__(self, f):
        self.f = {k: np.asarray(v) for k, v in f.items()}; self.tree = cKDTree(self.f["P"])

    def apply(self, X, k=4, skip_self=False):
        """X (n x 3, globally moved) -> corrected positions, the nearest anchor, its distance"""
        X = np.atleast_2d(X)
        d, j = self.tree.query(X, k=k + skip_self)
        if skip_self: d, j = d[:, 1:], j[:, 1:]
        w = 1 / (d ** 2 + 1e-8); w /= w.sum(1, keepdims=True)
        Y = self.f["s"][j][..., None] * np.einsum("nkij,nj->nki", self.f["R"][j], X) + self.f["t"][j]
        return np.einsum("nk,nkd->nd", w, Y), j[:, 0], d[:, 0]


def two_means(Q, iters=10):
    """split points in two: start from the two farthest apart"""
    d = np.linalg.norm(Q[:, None] - Q[None], axis=2); i, j = np.unravel_index(d.argmax(), d.shape)
    c = np.array([Q[i], Q[j]])
    for _ in range(iters):
        lab = np.linalg.norm(Q[:, None] - c[None], axis=2).argmin(1)
        c = np.array([Q[lab == k].mean(0) if (lab == k).any() else c[k] for k in (0, 1)])
    return lab, c


def unfold(t_ms, X, Y0, R0, inbox, P, Q, M, K=40, thr=0.11, min_n=6, sep_m=3.0, seed=0):
    """Where the map folded two parts of the course onto one (the evening map put the left hook's frames on the
    right arc), a frame there gets a candidate per part: its K nearest anchors (in the map) are split by their day
    positions into two groups and each group's similarity moves it. One candidate per frame is then chosen for the
    whole sequence by dynamic programming: the path whose speed (squared) sums least, so a pass through the folded
    part follows on from where the quad came from and where it goes. Frames outside (inbox False) keep Y0.
    X: globally moved positions (day-map units), Y0/R0: the field's answer, P/Q: anchors (moved / day map)."""
    rng = np.random.default_rng(seed); tree = cKDTree(P)
    cands = []
    for i in range(len(X)):
        c = [(Y0[i], R0[i])]
        if inbox[i]:
            _, nb = tree.query(X[i], k=min(K, len(P)))
            lab, cen = two_means(Q[nb])
            if np.linalg.norm(cen[0] - cen[1]) * M > sep_m:
                for g in (0, 1):
                    idx = nb[lab == g]
                    if len(idx) < min_n:
                        continue
                    a, b = P[idx], Q[idx]; best = None
                    for _ in range(60):
                        k = rng.choice(len(a), 3, replace=False)
                        try:
                            sc, R, tt = umeyama(a[k], b[k])
                        except np.linalg.LinAlgError:
                            continue
                        m = np.linalg.norm(sc * a @ R.T + tt - b, axis=1) < thr
                        if best is None or m.sum() > best.sum(): best = m
                    if best is None or best.sum() < min_n:
                        continue
                    sc, R, tt = umeyama(a[best], b[best])
                    c.append((sc * R @ X[i] + tt, R))
        cands.append(c)
    # dynamic programming over the sequence: cost = (step / dt)^2, no link over gaps of more than 0.5 s
    cost = [np.zeros(len(cands[0]))]; back = [None]
    for i in range(1, len(X)):
        dt = max((t_ms[i] - t_ms[i - 1]) / 1000, 1 / 30)
        prev = np.array([c[0] for c in cands[i - 1]]); cur = np.array([c[0] for c in cands[i]])
        if dt > 0.5:
            step = np.zeros((len(cur), len(prev)))
        else:
            step = (np.linalg.norm(cur[:, None] - prev[None], axis=2) * M / dt) ** 2
        tot = step + cost[-1][None]
        back.append(tot.argmin(1)); cost.append(tot.min(1))
    k = int(cost[-1].argmin()); pick = [k]
    for i in range(len(X) - 1, 0, -1):
        k = int(back[i][k]); pick.append(k)
    pick = pick[::-1]
    return [cands[i][pick[i]] for i in range(len(X))], sum(1 for i in range(len(X)) if pick[i] > 0)


def splat(field_npz, src, dst):
    F = Field(np.load(field_npz))
    raw = open(src, "rb").read()
    end = raw.index(b"end_header\n") + len(b"end_header\n")
    head = raw[:end].decode()
    props = [l.split()[-1] for l in head.splitlines() if l.startswith("property")]
    n = int([l for l in head.splitlines() if l.startswith("element vertex")][0].split()[-1])
    v = np.frombuffer(raw[end:], np.float32, n * len(props)).reshape(n, len(props)).copy()
    ix = {p: i for i, p in enumerate(props)}
    xyz = v[:, [ix["x"], ix["y"], ix["z"]]].astype(np.float64)
    Y, j, d = [], [], []
    for a in range(0, n, 200000):                                   # chunks: k nearest anchors of a million splats
        y, jj, dd = F.apply(xyz[a:a + 200000]); Y.append(y); j.append(jj); d.append(dd)
    Y, j = np.concatenate(Y), np.concatenate(j)
    v[:, [ix["x"], ix["y"], ix["z"]]] = Y
    for k in ("scale_0", "scale_1", "scale_2"):
        v[:, ix[k]] += np.log(F.f["s"][j])
    q = v[:, [ix["rot_1"], ix["rot_2"], ix["rot_3"], ix["rot_0"]]].astype(np.float64)   # wxyz -> xyzw
    q = (Rot.from_matrix(F.f["R"][j]) * Rot.from_quat(q)).as_quat()
    v[:, ix["rot_0"]], v[:, ix["rot_1"]], v[:, ix["rot_2"]], v[:, ix["rot_3"]] = q[:, 3], q[:, 0], q[:, 1], q[:, 2]
    open(dst, "wb").write(head.encode() + v.astype(np.float32).tobytes())
    print(f"{n} splats warped by {len(F.f['P'])} anchors -> {dst}")


if __name__ == "__main__":
    {"splat": splat}[sys.argv[1]](*sys.argv[2:])
