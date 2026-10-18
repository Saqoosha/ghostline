"""Accuracy of two replays of the same recordings (rt_replay_day.sh output dirs), without ground truth:
agreement on the frames both solved, distance to the course line (rt_trackline.py), and implausible jumps.
Split into frames both solved, and frames only one of them solved (are the gained answers right?).
  rt_replay_accuracy.py <dir A> <dir B> <track.json>"""
import glob, json, os, sys, numpy as np

def load(d):
    out = {}
    for f in glob.glob(f"{d}/*/*.jsonl"):
        k = (os.path.basename(os.path.dirname(f))[9:15], os.path.basename(f)[:-6])
        out[k] = {r["i"]: np.array(r["pos"]) for r in map(json.loads, open(f)) if "pos" in r}
    return out
A, B = load(sys.argv[1]), load(sys.argv[2]); na, nb = (os.path.basename(d.rstrip("/")) for d in sys.argv[1:3])
line = np.array(json.load(open(sys.argv[3]))["track"], float)
seg = np.linspace(0, 1, 20)[:, None, None]; dense = (line[:-1] * (1 - seg) + line[1:] * seg).reshape(-1, 3)   # 19 steps between line points
def to_line(P):
    P = np.asarray(P).reshape(-1, 3)
    return np.concatenate([np.sqrt(((c[:, None] - dense[None]) ** 2).sum(-1)).min(1) for c in np.array_split(P, max(1, len(P) // 2000))]) if len(P) else np.zeros(0)
def jumps(ans, keep):
    """frames in keep whose speed from the previous solved frame (<= 3 frames back) is over 40 m/s; and how many had one"""
    bad = n = 0
    for i in keep:
        prev = next((ans[j] for j in (i - 1, i - 2, i - 3) if j in ans), None)
        if prev is None: continue
        j = next(j for j in (i - 1, i - 2, i - 3) if j in ans); n += 1
        bad += np.linalg.norm(ans[i] - prev) / ((i - j) / 60) > 40   # frame numbers count the recording's 60 fps
    return bad, n
def pct(x, q): return float(np.percentile(x, q)) if len(x) else float("nan")
def report(name, ans_by_key, keys_by_key):
    P = [ans_by_key[k][i] for k in keys_by_key for i in keys_by_key[k]]; d = to_line(P)
    jb = jn = 0
    for k in keys_by_key: b, n = jumps(ans_by_key[k], keys_by_key[k]); jb += b; jn += n
    print(f"  {name:28} {len(P):7d}  line p50 {pct(d, 50):5.2f} p90 {pct(d, 90):5.2f} m  >3 m {np.mean(d > 3) * 100 if len(d) else 0:5.1f}%  >10 m {np.mean(d > 10) * 100 if len(d) else 0:5.1f}%"
          f"  jumps {jb / max(1, jn) * 100:4.1f}%")
keys = sorted(set(A) & set(B))
both = {k: sorted(set(A[k]) & set(B[k])) for k in keys}
diff = np.concatenate([[np.linalg.norm(A[k][i] - B[k][i]) for i in both[k]] for k in keys])
print(f"frames both solved: {len(diff)}; {na} vs {nb} answer distance p50 {pct(diff, 50):.3f} p90 {pct(diff, 90):.3f} p99 {pct(diff, 99):.2f} m, >1 m {np.mean(diff > 1) * 100:.1f}%")
print(f"{'':30} {'frames':>7}")
report(f"{na}, both solved", A, both); report(f"{nb}, both solved", B, both)
report(f"{na}, only {na}", A, {k: sorted(set(A[k]) - set(B[k])) for k in keys})
report(f"{nb}, only {nb}", B, {k: sorted(set(B[k]) - set(A[k])) for k in keys})
report(f"{na}, all", A, {k: sorted(A[k]) for k in keys}); report(f"{nb}, all", B, {k: sorted(B[k]) for k in keys})
