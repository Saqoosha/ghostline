"""Repair a race model where identical printed gates fooled the mapper: once per lap a stretch of every pilot's
track was attached to the wrong gate (the camera teleports 60-75 m and comes back 1-2 s later), and the gates those
frames saw were triangulated there as a ghost copy. Displaced frames: every step faster than VMAX m/s (the real
pilots never exceed ~35 m/s between 3 fps frames; a teleport is ~200 m/s) toggles the pilot between the main track
and the displaced copy, and the frames on the displaced side are removed with the 3D points they leave with fewer
than 2 observations (the ghost gate). The rest is bundle-adjusted with the gauge fixed to the existing poses and the
cameras constant, then the removed images are registered again by the incremental mapper with the existing frames
fixed. Frames that come back displaced are removed for good. Metres come from the fit's map scale.
usage: mapfix.py <work_dir> <in_model> <out_model> <fit.json>     (database.db and images/ under <work_dir>)
writes <out_model>_clean (before re-registration) and <out_model>."""
import json, os, sys, time
from collections import defaultdict
import numpy as np
import pycolmap as p

w, model_in, model_out, fit = sys.argv[1:5]
VMAX = float(os.environ.get("VMAX", 60))
S = json.load(open(fit))["scale"]
t0 = time.time()
log = lambda s: print(f"[{time.time() - t0:7.0f}s] {s}", flush=True)


def tracks(r):
    """{pilot: [(t_s, image)]} in time order, from the ms in the file names."""
    C = defaultdict(list)
    for im in r.images.values():
        C[im.name.split("/")[0]].append((int(im.name.split("/")[1][:-4]) / 1000, im))
    return {k: sorted(v, key=lambda x: x[0]) for k, v in sorted(C.items())}


def displaced(r):
    """{pilot: [image, ...]} on the displaced side, and the per-pilot step speeds (m/s)."""
    out, speeds = {}, {}
    for pilot, v in tracks(r).items():
        t = np.array([a for a, _ in v])
        X = np.array([im.projection_center() for _, im in v]) * S
        sp = np.linalg.norm(np.diff(X, axis=0), axis=1) / np.diff(t)
        side, bad = 0, []
        for i in range(1, len(v)):
            side ^= sp[i - 1] > VMAX
            if side:
                bad.append(v[i][1])
        out[pilot], speeds[pilot] = bad, sp
    return out, speeds


def report(r, tag):
    bad, speeds = displaced(r)
    for pilot, sp in speeds.items():
        print(f"  {tag} {pilot}: {len(sp) + 1} frames, speed p50 {np.median(sp):.1f} p90 {np.percentile(sp, 90):.1f} "
              f"max {sp.max():.1f} m/s, steps > {VMAX:.0f} m/s: {int((sp > VMAX).sum())}, displaced frames: {len(bad[pilot])}")
    return bad


def remove(r, bad):
    """deregister_frame also drops the 3D points left with < 2 observations; returns how many went."""
    n = r.num_points3D()
    for im in bad:
        r.deregister_frame(im.frame_id)
    return n - r.num_points3D()


def ba(r):
    """BA with the cameras constant (the gauge stays on the existing poses), then the mapper's own point filter:
    a few 2-view points run off during BA. Returns the number of observations filtered."""
    o = p.BundleAdjustmentOptions()
    o.refine_focal_length = o.refine_extra_params = o.refine_principal_point = False
    o.print_summary = False
    p.bundle_adjustment(r, o)
    n = p.ObservationManager(r).filter_all_points3D(4.0, 1.5)
    r.update_point_3d_errors()
    return n


r = p.Reconstruction(model_in)
log(f"{model_in}: {r.num_reg_images()} images, {r.num_points3D()} points")
bad = report(r, "in")
names = {im.name for v in bad.values() for im in v}
for pilot, v in bad.items():
    print(f"  remove {pilot}: {len(v)} frames at", ", ".join(f"{int(im.name.split('/')[1][:-4]) / 1000:.2f}" for im in v))
before = {im.name: im.projection_center() for im in r.images.values() if im.name not in names}
n_pt = remove(r, [im for v in bad.values() for im in v])
log(f"removed {len(names)} images and the {n_pt} points left with < 2 observations; {r.num_points3D()} points stay")
n_obs = ba(r)
drift = np.array([np.linalg.norm(r.images[i].projection_center() - before[r.images[i].name]) for i in r.reg_image_ids()]) * S
log(f"BA: kept cameras moved p50 {np.median(drift) * 100:.1f} cm, max {drift.max() * 100:.1f} cm; filtered {n_obs} observations; "
    f"{r.num_points3D()} points, mean reprojection error {r.compute_mean_reprojection_error():.3f} px")
os.makedirs(f"{model_out}_clean", exist_ok=True)
r.write(f"{model_out}_clean")

o = p.IncrementalPipelineOptions()
o.fix_existing_frames = True
o.multiple_models = False
o.ba_refine_focal_length = o.ba_refine_extra_params = False
o.mapper.abs_pose_refine_focal_length = o.mapper.abs_pose_refine_extra_params = False
o.mapper.abs_pose_min_num_inliers = int(os.environ.get("MIN_INL", 30))
os.makedirs(model_out, exist_ok=True)
recs = p.incremental_mapping(f"{w}/database.db", f"{w}/images", model_out, o, input_path=f"{model_out}_clean")
r = recs[0] if 0 in recs else max(recs.values(), key=lambda r: r.num_reg_images())
got = {im.name for im in r.images.values()}
log(f"re-registered {sum(n in got for n in names)}/{len(names)}; model {r.num_reg_images()} images, {r.num_points3D()} points")
bad = report(r, "out")
again = [im for v in bad.values() for im in v]
if again:
    print("  still displaced, removed for good:", ", ".join(im.name for im in again))
    n_pt = remove(r, again)
    ba(r)
    report(r, "final")
drift = np.array([np.linalg.norm(r.images[i].projection_center() - before[r.images[i].name]) for i in r.reg_image_ids()
                  if r.images[i].name in before]) * S
log(f"kept cameras vs input: moved p50 {np.median(drift) * 100:.1f} cm, max {drift.max() * 100:.1f} cm")
r.write(model_out)
log(f"{model_out}: {r.num_reg_images()} images, {r.num_points3D()} points, "
    f"mean reprojection error {r.compute_mean_reprojection_error():.3f} px")
