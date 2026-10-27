# as run on rt4090 for JDL R7 10/25: work dir ~/rt/night1025/mgp, clips in ../out (../out_yt), relay copy in ../relay (docs/night-rebuild.ja.md)
# the remaining flagged heats: their flight window (from the live answers) and their digital pilots (analog.py)
import json, os, subprocess, numpy as np
os.chdir("/home/saqoosha/rt/night1025/mgp")
done = {"133047-h28", "133512-h29", "141007-h33", "141806-h33", "135130-h30", "132543-h27", "135735-h31", "140232-h32", "131716-h25"}
rows = [l.split() for l in open("bad_all.txt") if l.strip()]; out = []
for r in rows:
    h, cells = r[0], r[1:]
    if h in done: continue
    d = f"../out/{h}" if os.path.isdir(f"../out/{h}") else f"../out_yt/{h}"
    start = json.load(open(f"{d}/race.json"))["start"]; L = json.load(open(f"../relay/20261025/{h}.json"))
    if "out_yt" in d: kind = {c: "digital" for c in ["R2", "F1", "R4", "R5"]}   # the quarterfinals (Expert, Pro), cut from the broadcast
    else:
        kind = {}
        for l in subprocess.run(["/home/saqoosha/mastenv/bin/python", "analog.py", d], capture_output=True, text=True).stdout.splitlines():
            p = l.split(); kind[p[1]] = p[2]
    fl = []
    for c, A in L["answers"].items():
        A = np.array(sorted(A, key=lambda x: x[0])); t = A[:, 0] / 1000; dt = np.diff(t); v = np.linalg.norm(np.diff(A[:, 1:4], axis=0), axis=1) / np.maximum(dt, 1e-3)
        m = (v > 3) & (v < 60) & (dt < 0.5)
        if m.sum() >= 50: fl += [t[1:][m].min(), t[1:][m].max()]
    if not fl: continue
    t0 = max(0.0, min(fl) - 3 - start / 1000); t1 = min(max(fl) + 2 - start / 1000, t0 + 150)
    dig = [c for c in ["R2", "F1", "R4", "R5"] if kind.get(c) == "digital"]; tgt = [c for c in cells if kind.get(c) == "digital"]
    if tgt: out.append(" ".join([h, f"{t0:.1f}", f"{t1 - t0:.1f}", ",".join(dig)] + tgt))
    print(h, {c: kind.get(c) for c in cells}, "->", tgt, f"window {t0:.0f}+{t1 - t0:.0f}s", flush=True)
open("plan.txt", "w").write("\n".join(out) + "\n"); print(len(out), "heats to do")
