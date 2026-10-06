# rt_power_an.py <dir with rt_power*.sh outputs> <tag...>: mean GPU / CPU-package power over the tracking window (first SPAN s after "tracking"), plus the run's summary
import sys, re, datetime as dt, zoneinfo, numpy as np, os
d = sys.argv[1]; SPAN = float(os.environ.get("SPAN", 74)); tz = zoneinfo.ZoneInfo(os.environ.get("TZ_LOG", "Asia/Tokyo"))
for tag in sys.argv[2:]:
    L = [l.split(" ", 1) for l in open(f"{d}/{tag}.log", errors="replace", encoding="utf-8-sig").read().splitlines() if " " in l]
    t0 = next(float(t) for t, s in L if s.startswith("tracking")); t1 = min(t0 + SPAN, next(float(t) for t, s in L if s.startswith("PIPE")))
    g = [[x.strip() for x in l.split(",")] for l in open(f"{d}/{tag}.gpu.csv").read().splitlines() if l.strip()]; g = [r for r in g if len(r) >= 6 and all(r)]
    gt = np.array([dt.datetime.strptime(r[0], "%Y/%m/%d %H:%M:%S.%f").replace(tzinfo=tz).timestamp() for r in g]); gv = np.array([[float(x) for x in r[1:6]] for r in g])
    m = (gt >= t0) & (gt <= t1); gw, clk, mclk, util, temp = gv[m].T
    c = np.array([[float(x) for x in l.split(",")] for l in open(f"{d}/{tag}.cpu.csv").read().splitlines() if l.strip()])
    cm = (c[:, 0] / 1000 >= t0) & (c[:, 0] / 1000 <= t1); cw = c[cm, 1] / 1000
    pipe = next((s for _, s in L if s.startswith("PIPE")), ""); st = re.search(r"ms p50 ([\d.]+) p90 ([\d.]+)", pipe)
    hz = re.findall(r"== \S+?_(\w+): processed \d+ of \d+ frames \(([\d.]+) Hz\), solved \d+ \(([\d.]+) Hz", "\n".join(s for _, s in L))
    lat = re.findall(r"latency ms .*?p50 ([\d.]+)", "\n".join(s for _, s in L)); acc = re.findall(r"solved frames .*?p50 ([\d.]+) p90 ([\d.]+)", "\n".join(s for _, s in L))
    print(f"{tag:12s} {t1-t0:5.0f}s  GPU {gw.mean():6.1f} W (p95 {np.percentile(gw,95):5.0f}, {clk.mean():5.0f} MHz, util {util.mean():3.0f}%)  CPU pkg {cw.mean():5.1f} W  "
          f"sum {gw.mean()+cw.mean():6.1f} W | cycle {st.group(1) if st else '-'}/{st.group(2) if st else '-'} ms | "
          + " ".join(f"{n} {p}/{s}Hz {l}ms {a[0]}/{a[1]}m" for (n, p, s), l, a in zip(hz, lat, acc)))
