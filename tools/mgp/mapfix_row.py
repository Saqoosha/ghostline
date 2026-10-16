"""Merge the two faces of a row of gates that the model holds as two copies, for the case where the copies are an
SfM artifact: the pilots see the row from both sides, the faces share no features (banner print on one face, bare
tubes on the other), and the cross-side ties are weak, so BA can leave the +y face and the -y face a few tens of cm
apart. The members are thin tubes, so a point of the +y face and the nearest point of the -y face at the same x and
height are the same line in space: such pairs (dx < DX, dz < DZ, 0.1 < dy < DY m, tracks without a common image) are
merged and the model is bundle-adjusted with the cameras constant, which moves the two sides' poses until they
agree; ROUNDS (3) rounds of pairing + BA, since each BA brings more pairs within reach.
NOT applied to the race-1 map (2026-10-16): there the "second copy" at gates 26/28..24/27 is the MultiGP banner
fence standing 0.4-0.5 m on the -y side of the row (its face stops at 1.6 m, the gate runs to 4.4 m; gate 29, with no
fence, has both faces within 3 cm). Merging it fused a real object into the gate and moved the nearby cameras ~19 cm.
Before using this, tabulate the per-upright gap of both faces along the whole row and look at a -y-side frame: a
gap that is the same on every upright of a gate and spans the gate's full height is drift; one that stops at a
fixed height, or differs between neighbouring uprights, is an object.
Box, in to_metric.py's track frame: x along the row, y across (the faces are split at y = ys).
usage: mapfix_row.py <in_model> <out_model> <top_frame.json> <fit.json> x0 x1 y0 y1 ys"""
import json, os, sys, time
import numpy as np
import pycolmap as p
from scipy.spatial import cKDTree

model_in, model_out, frame, fit = sys.argv[1:5]
x0, x1, y0, y1, YS = [float(v) for v in sys.argv[5:10]]
DX, DZ, DY = [float(os.environ.get(k, d)) for k, d in (("DX", 0.5), ("DZ", 0.15), ("DY", 0.8))]
t0 = time.time()
log = lambda s: print(f"[{time.time() - t0:7.0f}s] {s}", flush=True)
F, T = json.load(open(frame)), json.load(open(fit))
E, a0 = np.array([F["ex"], F["ey"], F["up"]]), np.array(F["origin"])
R2 = np.array(T["R"]) @ np.diag([1, T["mirror"]])
S = T["scale"]


def metric(X):
    G = (X - a0) @ E.T * S
    return np.c_[G[:, :2] @ R2.T + T["t"], G[:, 2]]


def faces(r):
    """ids and track-frame coords of the row's points (0.3..3 m up in the box), split by the side their cameras are on."""
    cy = {im.image_id: metric(im.projection_center()[None])[0, 1] for im in r.images.values()}
    ids = np.array(list(r.points3D))
    P = metric(np.array([r.points3D[i].xyz for i in ids]))
    k = (P[:, 2] > 0.3) & (P[:, 2] < 3.0) & (P[:, 0] > x0) & (P[:, 0] < x1) & (P[:, 1] > y0) & (P[:, 1] < y1)
    side = np.array([np.mean([cy[el.image_id] > YS for el in r.points3D[i].track.elements]) for i in ids[k]])
    return ids[k], P[k], side > 0.5


def thickness(r, tag):
    ids, P, A = faces(r)
    print(f"  {tag}: row points {len(P)}, +y face {int(A.sum())} y median {np.median(P[A, 1]):.2f}, "
          f"-y face {int((~A).sum())} y median {np.median(P[~A, 1]):.2f}, gap {np.median(P[A, 1]) - np.median(P[~A, 1]):.2f} m")


def merge(r):
    """pair each -y face point with the nearest unused +y face point at the same x and height; returns the new ids."""
    ids, P, A = faces(r)
    ia, ib = ids[A], ids[~A]
    tree = cKDTree(np.c_[P[A, 0] / DX, P[A, 2] / DZ])
    imgs = lambda i: {el.image_id for el in r.points3D[i].track.elements}
    cand = []
    for i, q in zip(ib, P[~A]):
        for k in tree.query_ball_point([q[0] / DX, q[2] / DZ], 1.0):
            dy = P[A][k, 1] - q[1]
            if 0.1 < dy < DY and not (imgs(int(ia[k])) & imgs(int(i))):
                cand.append((np.hypot((P[A][k, 0] - q[0]) / DX, (P[A][k, 2] - q[2]) / DZ), int(ia[k]), int(i)))
    cand.sort()
    used, pairs = set(), []
    for _, a, b in cand:
        if a not in used and b not in used:
            used.update((a, b)); pairs.append((a, b))
    log(f"merged {len(pairs)} face pairs ({len(ia)} +y and {len(ib)} -y points in the box)")
    return [r.merge_points3D(a, b) for a, b in pairs]


r = p.Reconstruction(model_in)
log(f"{model_in}: {r.num_reg_images()} images, {r.num_points3D()} points")
thickness(r, "in")
before = {im.name: im.projection_center() for im in r.images.values()}
o = p.BundleAdjustmentOptions()
o.refine_focal_length = o.refine_extra_params = o.refine_principal_point = False
o.print_summary = False
for rnd in range(int(os.environ.get("ROUNDS", 3))):      # each round pairs what the last BA brought within reach
    merged = merge(r)
    p.bundle_adjustment(r, o)
    r.update_point_3d_errors()
    e = np.array([r.points3D[i].error for i in merged if i in r.points3D])
    log(f"BA {rnd + 1}: merged points' reprojection error p50 {np.median(e):.2f} p90 {np.percentile(e, 90):.2f} max {e.max():.1f} px; "
        f"mean over the model {r.compute_mean_reprojection_error():.3f} px")
    n = p.ObservationManager(r).filter_all_points3D(4.0, 1.5)
    r.update_point_3d_errors()
    log(f"filtered {n} observations; {sum(i in r.points3D for i in merged)}/{len(merged)} merged points survive; "
        f"{r.num_points3D()} points, mean reprojection error {r.compute_mean_reprojection_error():.3f} px")
    thickness(r, f"round {rnd + 1}")
d = np.array([np.linalg.norm(im.projection_center() - before[im.name]) for im in r.images.values()]) * S
cx = np.array([metric(im.projection_center()[None])[0] for im in r.images.values()])
near = (cx[:, 0] > x0 - 5) & (cx[:, 0] < x1 + 5) & (cx[:, 1] > y0 - 8) & (cx[:, 1] < y1 + 8)
log(f"cameras moved p50 {np.median(d) * 100:.1f} cm, max {d.max() * 100:.1f} cm; near the row ({int(near.sum())}) "
    f"p50 {np.median(d[near]) * 100:.1f} cm, max {d[near].max() * 100:.1f} cm")
thickness(r, "out")
os.makedirs(model_out, exist_ok=True)
r.write(model_out)
log(f"{model_out}: {r.num_reg_images()} images, {r.num_points3D()} points")
