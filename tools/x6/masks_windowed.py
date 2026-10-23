"""Masks for training a hand-held X6 walk: the operator held the stick differently over the walk, so "where a person always is"
is counted over a window of neighbouring frames, not the whole walk. A pixel is dropped in a frame when it is outside the
rig circle, within DIL px of a person SAM marked in that frame, or a person in more than THR of the frames within +-WIN.
  python masks_windowed.py <work dir> <prefix: v009> <out: masks3> [WIN=45] [THR=0.25] [DIL=15]
writes <work>/scene/<out>/<prefix>/cam{0,1}/*.png and prints the static share over time"""
import glob, os, sys
import numpy as np
from PIL import Image
from scipy import ndimage
W, P, OUT = sys.argv[1:4]; WIN = int(sys.argv[4]) if len(sys.argv) > 4 else 45
THR = float(sys.argv[5]) if len(sys.argv) > 5 else 0.25; DIL = int(sys.argv[6]) if len(sys.argv) > 6 else 15
S = 480                                                    # the count is made small, then grown back
for t in (0, 1):
    circle = np.array(Image.open(f"{W}/probe/rig{t}.png")) > 0
    small_c = np.array(Image.fromarray(circle.astype(np.uint8) * 255).resize((S, S), Image.NEAREST)) > 0
    files = sorted(glob.glob(f"{W}/scene/masks/{P}/cam{t}/*.png"))
    person = np.stack([(np.array(Image.open(p).resize((S, S), Image.NEAREST)) == 0) & small_c for p in files])   # n x S x S
    csum = np.concatenate([np.zeros((1, S, S), np.int32), np.cumsum(person, axis=0, dtype=np.int32)])
    os.makedirs(f"{W}/scene/{OUT}/{P}/cam{t}", exist_ok=True); share = []
    for i, p in enumerate(files):
        a, b = max(0, i - WIN), min(len(files), i + WIN + 1)
        static = ndimage.binary_dilation((csum[b] - csum[a]) / (b - a) > THR, iterations=6)
        static = np.array(Image.fromarray(static.astype(np.uint8) * 255).resize(circle.shape[::-1], Image.NEAREST)) > 0
        drop = np.array(Image.open(p)) == 0
        drop = ndimage.binary_dilation(drop & circle, iterations=DIL) | static | ~circle
        Image.fromarray(np.where(drop, 0, 255).astype(np.uint8)).save(p.replace("/masks/", f"/{OUT}/"))
        share.append((static & circle).sum() / circle.sum())
    s = np.array(share); q = len(s) // 8
    print(f"cam{t}: {len(files)} frames, static inside the circle p10 {np.percentile(s, 10)*100:.1f}% p50 {np.median(s)*100:.1f}% p90 {np.percentile(s, 90)*100:.1f}%;"
          " by eighths " + " ".join(f"{s[k*q:(k+1)*q].mean()*100:.0f}" for k in range(8)), flush=True)
