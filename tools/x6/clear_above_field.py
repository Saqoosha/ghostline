"""A scene without what floats over the field: splats more than CLEAR m above the ground inside the course's box (+MARGIN m)
go, everything outside the box stays (trees, buildings). The walk saw the field only from the side, so splats hanging over it
cost nothing from there but cover the ground as white haze from straight above. The ground is the median height of the
splats near the walk's floor inside the box, per 5 m cell. Scene and course line in the y-up frame (x east, y up, z south).
  python clear_above_field.py <in.ply> <course track.json> <out.ply> [CLEAR=3.5] [MARGIN=8]"""
import json, sys
import numpy as np
from plyfile import PlyData, PlyElement
src, course, out = sys.argv[1:4]; clear = float(sys.argv[4]) if len(sys.argv) > 4 else 3.5; margin = float(sys.argv[5]) if len(sys.argv) > 5 else 8
t = np.array(json.load(open(course))["track"]); lo, hi = t[:, [0, 2]].min(0) - margin, t[:, [0, 2]].max(0) + margin
ply = PlyData.read(src); v = ply["vertex"].data; xyz = np.c_[v["x"], v["y"], v["z"]]
inside = np.all((xyz[:, [0, 2]] >= lo) & (xyz[:, [0, 2]] <= hi), axis=1)
op = 1 / (1 + np.exp(-v["opacity"]))
# the ground per 5 m cell: the densest 0.1 m layer of the solid splats there (a low percentile follows the junk under the grass)
cell = np.floor((xyz[:, [0, 2]] - lo) / 5).astype(int); nx, nz = cell.max(0) + 1
key = cell[:, 0] * nz + cell[:, 1]; solid = inside & (op > 0.5)
ground = np.full(nx * nz, np.nan)
o = np.argsort(key[solid]); ks, ys = key[solid][o], xyz[solid, 1][o]; cuts = np.flatnonzero(np.diff(ks)) + 1
for k, y in zip(ks[np.r_[0, cuts]], np.split(ys, cuts)):
    if len(y) < 200: continue
    h, e = np.histogram(y, bins=np.arange(y.min(), y.max() + 0.2, 0.1)); ground[k] = e[h.argmax()] + 0.05
gfill = np.where(np.isnan(ground), np.nanmedian(ground), ground)
drop = inside & (xyz[:, 1] > gfill[np.clip(key, 0, nx * nz - 1)] + clear)
print(f"box x {lo[0]:.0f}..{hi[0]:.0f} z {lo[1]:.0f}..{hi[1]:.0f}; ground p10/p50/p90 {np.nanpercentile(ground, 10):.2f}/{np.nanmedian(ground):.2f}/{np.nanpercentile(ground, 90):.2f} m; "
      f"{inside.sum():,} splats inside, {drop.sum():,} more than {clear} m above the ground dropped, {len(v) - drop.sum():,} kept")
PlyData([PlyElement.describe(v[~drop], "vertex")], text=False).write(out)
print("->", out)
