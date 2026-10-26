"""The stretch of a DJI clip flown above a height (its .SRT rel_alt, metres over takeoff): the first and last second, for ffmpeg -ss/-to.
  python dji_high_span.py <clip.SRT> [MIN=10]"""
import re, sys
t, a = [], []
for blk in open(sys.argv[1]).read().split("\n\n"):
    m = re.search(r"(\d+):(\d+):(\d+),(\d+) -->", blk); g = re.search(r"rel_alt:\s*(-?[\d.]+)", blk)
    if m and g: t.append(int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000); a.append(float(g[1]))
lo = float(sys.argv[2]) if len(sys.argv) > 2 else 10
hi = [k for k, v in enumerate(a) if v > lo]
print(f"{t[hi[0]]:.2f} {t[hi[-1]]:.2f}  ({t[hi[-1]] - t[hi[0]]:.1f} s above {lo} m of {t[-1]:.1f} s; max {max(a):.1f} m)")
