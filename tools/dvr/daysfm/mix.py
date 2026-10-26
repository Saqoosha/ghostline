"""per pilot: the fused (MGP-style) answers where the live ones were off the course (line p50 > 3 m or p90 > 8 m) and the
fused p90 is under 0.7 of the live one; the live answers otherwise. Writes a relay dir with only the heats that changed."""
import json, os, sys, numpy as np
live_dir, fused_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]; heats = [a.split(":")[0] for a in sys.argv[4:]]
line = np.array(json.load(open("/home/saqoosha/rt/tracks/jdl-2026-r7-v1.json"))["track"])[::2][:, [0, 2]]
def dist(rows, w0, w1):
    r = np.array(rows); r = r[(r[:, 0] >= w0) & (r[:, 0] < w1)]
    return np.min(np.linalg.norm(r[:, None, [1, 3]] - line[None], axis=2), axis=1) if len(r) else np.array([np.inf])
def fly(rows):                                       # the answers in flight (3-60 m/s, as heat_report.py): the pads are off the line
    A = np.array(sorted(rows, key=lambda r: r[0])); t = A[:, 0] / 1000; P = A[:, 1:4]; dt = np.diff(t)
    v = np.linalg.norm(np.diff(P, axis=0), axis=1) / np.maximum(dt, 1e-3); m = (v > 3) & (v < 60) & (dt < 0.5)
    return np.min(np.linalg.norm(P[1:][m][::2, None, [0, 2]] - line[None], axis=2), axis=1) if m.sum() >= 50 else np.array([np.inf])
os.makedirs(f"{out}/20261025", exist_ok=True); ev = []
for hid in heats:
    L = json.load(open(f"{live_dir}/20261025/{hid}.json")); F = json.load(open(f"{fused_dir}/20261025/{hid}.json")); M = dict(L, answers=dict(L["answers"])); used = []
    for c in L["order"]:
        if c not in L["answers"] or not F["answers"].get(c): continue
        dl, df = fly(L["answers"][c]), fly(F["answers"][c])
        print("  ", hid, c, "live p50 %.2f p90 %.2f  fused p50 %.2f p90 %.2f" % (np.median(dl), np.percentile(dl, 90), np.median(df), np.percentile(df, 90)))
        # the race window holds the pads (20 m off the line) at both ends, so p90 cannot tell: the median
        if np.median(dl) > 3 and np.median(df) < 0.6 * np.median(dl): M["answers"][c] = F["answers"][c]; used.append(c)
    print(hid, "mgp for", used or "none", flush=True)
    if used:
        json.dump(M, open(f"{out}/20261025/{hid}.json", "w"), ensure_ascii=False, separators=(",", ":"))
        for l in open(f"{live_dir}/heats-20261025.jsonl"):
            if json.loads(l)["id"] == hid: ev.append(l)
open(f"{out}/heats-20261025.jsonl", "w").write("".join(ev))
