"""Draw a learned-feature SfM model of race feeds from above (its cameras' best-fit plane), one panel per feed: each
feed's camera path in time order (blue -> red) over every camera of the model (grey). A sound model draws laps of one
course in every panel; a broken one draws stars or folded copies.
usage: night_model_plot.py <model dir> <out.jpg> [feed ...]"""
import sys
import numpy as np, cv2
import pycolmap

rec = pycolmap.Reconstruction(sys.argv[1]); out = sys.argv[2]; want = sys.argv[3:]
feeds = {}
for im in rec.images.values():
    feed, f = im.name.split("/")
    feeds.setdefault(feed, []).append((int(f[:7]), im.projection_center()))
C = np.array([c for v in feeds.values() for _, c in v]); c0 = np.median(C, 0)
_, _, Vt = np.linalg.svd(C - c0); P = lambda X: (X - c0) @ Vt[:2].T
A = P(C); r = np.linalg.norm(A - np.median(A, 0), axis=1)
core = A[r < 4 * np.median(r)]                          # a few cameras flung far off must not shrink the course to a dot
lo, hi = core.min(0), core.max(0); span = (hi - lo).max() * 1.1; mid = (lo + hi) / 2
print(f"{(r >= 4 * np.median(r)).sum()} of {len(A)} cameras beyond 4x the median distance from the centre")
S = 420
px = lambda a: tuple(int(v) for v in ((a - mid) / span * S + S / 2))
panels = []
for feed in (want or sorted(feeds)):
    if feed not in feeds:
        continue
    img = np.full((S, S, 3), 255, np.uint8)
    for a in A: cv2.circle(img, px(a), 1, (215, 215, 215), -1)
    v = sorted(feeds[feed], key=lambda x: x[0]); B = P(np.array([c for _, c in v]))
    for i in range(len(B) - 1):
        col = cv2.applyColorMap(np.uint8([[int(255 * i / max(1, len(B) - 1))]]), cv2.COLORMAP_JET)[0, 0].tolist()
        cv2.line(img, px(B[i]), px(B[i + 1]), col, 2)
    cv2.putText(img, f"{feed} ({len(v)})", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
    panels.append(img)
cols = 4; rows = [np.hstack(panels[i:i + cols] + [np.full((S, S, 3), 255, np.uint8)] * (cols - len(panels[i:i + cols]))) for i in range(0, len(panels), cols)]
cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
print(f"{len(panels)} panels -> {out}")
