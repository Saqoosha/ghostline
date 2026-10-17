"""Compare replays of the same recordings (rt_replay_day.sh output dirs): per run, the frames processed while a cell had a
picture, the share solved, inliers, relocalizations and latency; per recording and cell, and in all.
  rt_replay_compare.py <dir A> <dir B> [...]      (each dir holds <recording>/<cell>.jsonl)"""
import glob, json, os, sys, numpy as np

def load(d):
    out = {}
    for f in glob.glob(f"{d}/*/*.jsonl"):
        rows = [json.loads(l) for l in open(f)]
        if rows: out[(os.path.basename(os.path.dirname(f))[9:15], os.path.basename(f)[:-6])] = rows
    return out
def stats(rows):
    sol = [r for r in rows if "pos" in r]; inl = [r["inl"] for r in sol]; lat = [r["lat"] for r in rows if r.get("lat") is not None]
    return dict(n=len(rows), solved=len(sol), share=len(sol) / max(1, len(rows)), inl=float(np.median(inl)) if inl else 0,
                reloc=sum(r.get("how") == "reloc" for r in sol), lat=float(np.median(lat)) if lat else 0)
runs = {d: load(d) for d in sys.argv[1:]}; keys = sorted(set.intersection(*[set(r) for r in runs.values()]))
names = [os.path.basename(d.rstrip("/")) for d in sys.argv[1:]]
print(f"{'rec':6} {'cell':4} " + "  ".join(f"{n:>34}" for n in names)); print(" " * 12 + "  ".join(f"{'frames  solved   inl  reloc   lat':>34}" for _ in names))
tot = {d: [] for d in runs}
for k in keys:
    line = f"{k[0]:6} {k[1]:4} "
    for d in runs:
        s = stats(runs[d][k]); tot[d] += runs[d][k]
        line += f"  {s['n']:7d} {s['share'] * 100:6.1f}% {s['inl']:5.0f} {s['reloc']:5d} {s['lat']:5.1f}ms"
    print(line)
print("all" + " " * 9 + "  ".join(f"  {s['n']:7d} {s['share'] * 100:6.1f}% {s['inl']:5.0f} {s['reloc']:5d} {s['lat']:5.1f}ms" for s in (stats(tot[d]) for d in runs)))
