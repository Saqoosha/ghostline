"""A DJI clip's own GPS (its .SRT: latitude, longitude, rel_alt per frame) as a rough course line, from a start time to the end.
Metres east / north / up around a reference point (lat0 lon0: the RTK SfM's origin, so the line sits in the scan's frame
once the up axis is turned the same way), every DT seconds. Only as good as the drone's GPS (metres), for a first look.
  python dji_srt_track.py <clip.SRT> <start s> <out.json> [lat0 lon0] [DT=0.1]"""
import json, math, re, sys
srt, t0, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
lat0 = float(sys.argv[4]) if len(sys.argv) > 5 else None; lon0 = float(sys.argv[5]) if len(sys.argv) > 5 else None
dt = float(sys.argv[6]) if len(sys.argv) > 6 else 0.1
rows = []
for blk in open(srt).read().split("\n\n"):
    m = re.search(r"(\d+):(\d+):(\d+),(\d+) -->", blk); g = re.search(r"latitude:\s*(-?[\d.]+)\].*?longitude:\s*(-?[\d.]+)\].*?rel_alt:\s*(-?[\d.]+)", blk, re.S)
    if m and g:
        t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000
        if t >= t0: rows.append((t, float(g[1]), float(g[2]), float(g[3])))
if lat0 is None: lat0, lon0 = rows[0][1], rows[0][2]
R = 6378137.0; k = math.cos(math.radians(lat0))
pts, last = [], -1e9
for t, la, lo, alt in rows:
    if t - last < dt - 1e-6: continue
    last = t; pts.append([round(math.radians(lo - lon0) * R * k, 3), round(math.radians(la - lat0) * R, 3), round(alt, 3)])
json.dump(dict(source=srt.split("/")[-1], start=t0, ref=[lat0, lon0], enu=pts), open(out, "w"))
e = [p[0] for p in pts]; n = [p[1] for p in pts]; u = [p[2] for p in pts]
L = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
print(f"{len(pts)} points over {rows[-1][0] - rows[0][0]:.1f} s; east {min(e):.1f}..{max(e):.1f} m, north {min(n):.1f}..{max(n):.1f} m, "
      f"up {min(u):.1f}..{max(u):.1f} m; path {L:.0f} m -> {out}")
