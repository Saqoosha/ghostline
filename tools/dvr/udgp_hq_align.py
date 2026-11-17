"""Sim3-align a new SfM onto the UDGP 2026 R3 scan's SfM through the camera centres of the 360 frames both share
by name, so a rebuilt scan lands in the frame every tracked answer, the keyframe map and the room polygon are in.

    python udgp_hq_align.py <new sparse/0> <old sparse/0> <out dir>
"""
import os
import sys

import numpy as np
import pycolmap

new_dir, old_dir, out_dir = sys.argv[1:4]
new, old = pycolmap.Reconstruction(new_dir), pycolmap.Reconstruction(old_dir)
by_name = {im.name: im for im in old.images.values()}
pairs = [(im, by_name[im.name]) for im in new.images.values() if im.name in by_name]
src = np.array([a.projection_center() for a, _ in pairs])
dst = np.array([b.projection_center() for _, b in pairs])
print(f"shared images {len(pairs)} (old {len(old.images)}, new {len(new.images)})")
if len(pairs) < 100:
    sys.exit("too few shared images to align")

# Umeyama, then once more without the worst 10% (a few frames the two SfMs disagree on)
def umeyama(a, b):
    ma, mb = a.mean(0), b.mean(0)
    A, B = a - ma, b - mb
    U, S, Vt = np.linalg.svd(B.T @ A / len(a))
    D = np.eye(3)
    D[2, 2] = np.sign(np.linalg.det(U @ Vt))
    R = U @ D @ Vt
    s = (S * np.diag(D)).sum() / (A ** 2).sum(1).mean()
    return s, R, mb - s * R @ ma

s, R, t = umeyama(src, dst)
res = np.linalg.norm((s * (R @ src.T)).T + t - dst, axis=1)
keep = res <= np.percentile(res, 90)
s, R, t = umeyama(src[keep], dst[keep])
res = np.linalg.norm((s * (R @ src.T)).T + t - dst, axis=1)
print(f"scale {s:.5f}  centre error p50 {np.median(res) * 100:.1f} cm  p90 {np.percentile(res, 90) * 100:.1f} cm"
      f"  max {res.max() * 100:.1f} cm")
if np.median(res) > 0.10:
    print("WARNING: the two SfMs disagree by more than 10 cm at the median (fine for viewing, not for the tracked answers)")

new.transform(pycolmap.Sim3d(s, pycolmap.Rotation3d(R), t))
os.makedirs(out_dir, exist_ok=True)
new.write(out_dir)
print("wrote", out_dir)
print(new.summary())
