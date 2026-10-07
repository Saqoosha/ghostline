# rt_eval_draw.py <answers.jsonl> <truth.json> <fps> [delay s]: what the live page draws (viewer/src/live.ts 'delay' dot: the frame
# `delay` ago, a weighted quadratic through the answers within 0.25 s that have arrived), scored against the offline path,
# once with the raw answers and once with the tracker's BA revisions ("win") applied as they arrive.
import sys, json, numpy as np
from scipy.spatial.transform import Rotation as Rot, Slerp
rows = [json.loads(l) for l in open(sys.argv[1])]; fps = float(sys.argv[3]); delay = float(sys.argv[4]) if len(sys.argv) > 4 else 0.1
tr = {p["i"]: p for p in json.load(open(sys.argv[2]))["poses"] if p.get("src") in ("ba", "ba-fill", "takeoff", "ground")}
rows.sort(key=lambda r: r["done"]); W = 0.25 * fps; LOST = 0.3
def fit(ans, j):
    x = np.array([i - j for i in ans]); m = np.abs(x) <= W
    if m.sum() < 4: return None
    ks = [i for i, k in zip(ans, m) if k]; x = x[m].astype(float); w = np.sqrt(np.minimum([ans[i][2] for i in ks], 400) / 200)
    A = np.stack([np.ones_like(x), x, x * x], 1) * w[:, None]; P = np.array([ans[i][0] for i in ks]) * w[:, None]
    try: return np.linalg.lstsq(A, P, rcond=None)[0][0]
    except np.linalg.LinAlgError: return None
def run(use_win):
    ans, k, out = {}, 0, {}
    for j in sorted(tr):
        t = j / fps + delay
        while k < len(rows) and rows[k]["done"] <= t:
            r = rows[k]; k += 1
            if r.get("how", "none") != "none": ans[r["i"]] = [np.array(r["pos"]), np.array(r["quat"]), r["inl"]]
            if use_win:
                for w in r.get("win", []):
                    if w[0] in ans: ans[w[0]][0] = np.array(w[1:4]); ans[w[0]][1] = np.array(w[4:8])
        near = {i: ans[i] for i in range(int(j - 2 * W - 1), int(j + 2 * W + 2)) if i in ans}
        before = max((i for i in near if i <= j), default=None); after = min((i for i in near if i >= j), default=None)
        if before is not None and after is not None and after - before <= 2 * W:
            p = fit(near, j); p = near[before][0] if p is None else p
            q = near[before][1] if after == before else Slerp([before, after], Rot.from_quat([near[before][1], near[after][1]]))(j).as_quat()
            out[j] = (p, q, "interp")
        else:
            b = next((i for i in range(j, j - int(LOST * fps) - 1, -1) if i in ans), None)
            if b is not None: out[j] = (ans[b][0], ans[b][1], "hold")
    return out
def score(out, name):
    js = sorted(out); e = np.array([out[j][0] - tr[j]["pos"] for j in js]); d = np.linalg.norm(e, axis=1)
    rot = np.degrees([(Rot.from_quat(out[j][1]).inv() * Rot.from_quat(tr[j]["quat"])).magnitude() for j in js])
    wob = [np.linalg.norm(out[j][0] - tr[j]["pos"] - (out[j - 1][0] - tr[j - 1]["pos"] + out[j + 1][0] - tr[j + 1]["pos"]) / 2) for j in js if j - 1 in out and j + 1 in out and out[j][2] == out[j - 1][2] == out[j + 1][2] == "interp"]
    pc = lambda a, q: float(np.percentile(a, q))
    print(f"  {name:8s} drawn {len(js)}/{len(tr)} ({np.mean([out[j][2] == 'interp' for j in js]) * 100:.0f}% interp)  pos m p50 {pc(d, 50):.3f} p90 {pc(d, 90):.3f} p99 {pc(d, 99):.2f}  rot deg p50 {pc(rot, 50):.2f} p90 {pc(rot, 90):.2f}  wobble cm p50 {pc(wob, 50) * 100:.2f} p90 {pc(wob, 90) * 100:.2f}")
print(sys.argv[1].split("/")[-1], f"answers {sum(r.get('how', 'none') != 'none' for r in rows)}, with win {sum('win' in r for r in rows)}")
score(run(False), "raw")
if any("win" in r for r in rows): score(run(True), "BA")
