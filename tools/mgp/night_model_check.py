"""Is a learned-feature SfM model of race feeds sane? Per feed, consecutive frames (names <feed>/<t_ms>.jpg, neighbours
within 0.5 s): a flying quad moves smoothly, so a frame far off the midpoint of its two neighbours is a spike
(off by more than twice half the neighbours' distance plus a fifth of the feed's median step). "Jumps" (a step over 3x
the median step) misread a feed that waits on the pad: its median step is ~0 and a normal take-off counts as jumps
(a sound snapshot read 12.8%, night_model_plot.py showed laps of one course in every feed). The first GLOMAP model of the evening (initial cameras f=357, no distortion, against the refined
f~300, k1~0.13) had 1.15 px reprojection error and right rotations, yet its positions were a star around one point:
p90 steps 3-15x the median. Also prints the extent (p5-p95) of each feed's cameras in model units.
usage: night_model_check.py <model dir>"""
import sys
import numpy as np
import pycolmap

rec = pycolmap.Reconstruction(sys.argv[1])
feeds = {}
for im in rec.images.values():
    feed, f = im.name.split("/")
    feeds.setdefault(feed, []).append((int(f[:7]), im.projection_center()))
print(f"{rec.num_reg_images()} frames, {rec.num_points3D()} points, {rec.compute_mean_reprojection_error():.2f} px")
allj = []
for feed in sorted(feeds):
    v = sorted(feeds[feed], key=lambda x: x[0])
    t = np.array([x[0] for x in v]) / 1000; C = np.array([x[1] for x in v])
    d = np.linalg.norm(np.diff(C, axis=0), axis=1); ok = np.diff(t) <= 0.5
    if ok.sum() < 5:
        continue
    med = np.median(d[ok]); jumps = (d[ok] > 3 * med).mean()
    tri = (np.diff(t)[:-1] <= 0.5) & (np.diff(t)[1:] <= 0.5)
    dev = np.linalg.norm(C[1:-1] - (C[:-2] + C[2:]) / 2, axis=1); chord = np.linalg.norm(C[2:] - C[:-2], axis=1) / 2
    spikes = (dev > 2 * (chord + 0.2 * med))[tri].mean() if tri.any() else 0.0; allj.append(spikes)
    ext = np.percentile(C, 95, 0) - np.percentile(C, 5, 0)
    print(f"{feed:9s} {len(v):5d} frames  step p50 {med:.3f} p90 {np.percentile(d[ok], 90):.3f}  spikes {spikes:5.1%}  (jumps {jumps:5.1%})  extent {ext.round(2)}")
print(f"spikes over all feeds: median {np.median(allj):.1%}")
