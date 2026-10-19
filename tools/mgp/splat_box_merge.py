"""Two scans of the same geometry (night 3DGS trained from the day scan with the positions frozen) joined by place: the
splats of <inside.ply> within a box of the course, those of <outside.ply> elsewhere. The night scan t5 (trained with the
folded part's frames) draws the double gates there, t4 the rest of the course better.
usage: splat_box_merge.py <outside.ply> <inside.ply> <top_frame.json> <fit.json> <out.ply>
env: BOX (track frame m: x0,x1,y0,y1; -138,-104,22,60 = the folded part), MARGIN (2 m)"""
import json, os, sys
import numpy as np
from plyfile import PlyData, PlyElement

outside, inside, frame_json, fit_json, out = sys.argv[1:6]
x0, x1, y0, y1 = [float(v) for v in os.environ.get("BOX", "-138,-104,22,60").split(",")]
m = float(os.environ.get("MARGIN", 2.0))
F, Tf = json.load(open(frame_json)), json.load(open(fit_json))
E, a0 = np.array([F["ex"], F["ey"], F["up"]]), np.array(F["origin"]); R2 = np.array(Tf["R"]) @ np.diag([1, Tf["mirror"]]); U = float(Tf["scale"])
def inbox(v):                                         # map -> track frame (x = VD x, y = VD z), metres
    g = ((np.c_[v["x"], v["y"], v["z"]] - a0) @ E.T * U)[:, :2] @ R2.T + Tf["t"]
    return (g[:, 0] > x0 - m) & (g[:, 0] < x1 + m) & (g[:, 1] > y0 - m) & (g[:, 1] < y1 + m)
a, b = PlyData.read(outside)["vertex"].data, PlyData.read(inside)["vertex"].data
assert a.dtype == b.dtype, "the two scans must have the same splat fields"
ka, kb = ~inbox(a), inbox(b)
PlyData([PlyElement.describe(np.concatenate([a[ka], b[kb]]), "vertex")]).write(out)
print(f"{ka.sum():,} splats outside the box, {kb.sum():,} inside -> {out}")
