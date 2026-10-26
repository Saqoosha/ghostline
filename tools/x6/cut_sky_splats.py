"""Splats that float in front of the sky: a scan trained with the sky masked out still leaves haze and shards past the walk's
reach (too few views to pin them), and from the cameras they sit on the sky. Each splat's centre is projected into every
STEP-th X6 frame (OPENCV_FISHEYE, as sky_pano.py) and looked up in that frame's sky mask (white = sky); a splat seen in at
least MINVIEWS frames and on the sky in at least FRAC of them goes. A tree or a building sits on its own pixels, not the sky's.
Only splats outside the course box (+MARGIN m) or HIGH m above the ground are tested: the field itself is never cut.
Scene in the y-up frame; the SfM in ENU (enu2yup.json takes one to the other).
  python cut_sky_splats.py <in.ply> <sparse dir> <image root> <sky mask root> <enu2yup.json> <course track.json> <out.ply>
  env STEP (10), FRAC (0.6), MINVIEWS (5), MARGIN (5), HIGH (3), GROUND (-2.0)"""
import json, os, sys, numpy as np, torch, pycolmap
from PIL import Image
from plyfile import PlyData, PlyElement
src, sparse, imroot, mroot, frame, course, out = sys.argv[1:8]
E = lambda k, d: float(os.environ.get(k, d))
STEP, FRAC, MINV, MARGIN, HIGH, GROUND = int(E("STEP", 10)), E("FRAC", 0.6), int(E("MINVIEWS", 5)), E("MARGIN", 5), E("HIGH", 3), E("GROUND", -2.0)
dev = "cuda"
ply = PlyData.read(src); v = ply["vertex"].data
xyz = np.c_[v["x"], v["y"], v["z"]].astype(np.float64)
t = np.array(json.load(open(course))["track"]); lo, hi = t[:, [0, 2]].min(0) - MARGIN, t[:, [0, 2]].max(0) + MARGIN
outside = ~np.all((xyz[:, [0, 2]] >= lo) & (xyz[:, [0, 2]] <= hi), axis=1)
test = outside | (xyz[:, 1] > GROUND + HIGH)
F = np.array(json.load(open(frame))["R"], float)            # ENU -> y-up; its inverse takes the scene back to the SfM's frame
P = torch.tensor(xyz[test] @ F, dtype=torch.float32, device=dev)   # (F^T)^-1 = F for a rotation: row vectors times F
print(f"{len(v):,} splats, {test.sum():,} tested (outside the course box or {HIGH:g} m up)", flush=True)
rec = pycolmap.Reconstruction(sparse)
seen = torch.zeros(len(P), dtype=torch.int32, device=dev); sky = torch.zeros_like(seen)
imgs = sorted((im for im in rec.images.values() if "/cam" in im.name), key=lambda im: im.name)[::STEP]
for n, im in enumerate(imgs):
    mp = os.path.join(mroot, os.path.splitext(im.name)[0] + ".png")
    if not os.path.exists(mp): continue
    cam = rec.cameras[im.camera_id]; fx, fy, cx, cy, k1, k2, k3, k4 = cam.params
    m = torch.tensor(np.asarray(Image.open(mp).convert("L")) > 127, device=dev)
    sx, sy = m.shape[1] / cam.width, m.shape[0] / cam.height
    T = im.cam_from_world() if callable(im.cam_from_world) else im.cam_from_world
    R = torch.tensor(T.rotation.matrix(), dtype=torch.float32, device=dev); tr = torch.tensor(T.translation, dtype=torch.float32, device=dev)
    c = P @ R.T + tr
    r = torch.hypot(c[:, 0], c[:, 1]); th = torch.atan2(r, c[:, 2]); t2 = th * th
    rd = th * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * k4)))); s = torch.where(r > 1e-9, rd / r.clamp_min(1e-9), torch.zeros_like(r))
    u = (fx * c[:, 0] * s + cx) * sx; w = (fy * c[:, 1] * s + cy) * sy
    ok = (th < np.radians(95)) & (u >= 0) & (u < m.shape[1] - 1) & (w >= 0) & (w < m.shape[0] - 1) & (torch.linalg.norm(c, dim=1) > 1)
    on = torch.zeros_like(ok); on[ok] = m[w[ok].long(), u[ok].long()]
    seen += ok.int(); sky += on.int()
    if n % 50 == 0: print(f"  {n}/{len(imgs)} frames", flush=True)
seen, sky = seen.cpu().numpy(), sky.cpu().numpy()
cut_t = (seen >= MINV) & (sky >= FRAC * seen)
cut = np.zeros(len(v), bool); cut[np.flatnonzero(test)[cut_t]] = True
PlyData([PlyElement.describe(v[~cut], "vertex")], text=False).write(out)
print(f"cut {cut.sum():,} splats on the sky in {FRAC:.0%}+ of {MINV}+ views ({len(imgs)} frames), kept {(~cut).sum():,} -> {out}", flush=True)
