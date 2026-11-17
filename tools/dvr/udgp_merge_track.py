"""Join rt_track.py's forward run and its run on the reversed video (udgp_reverse.sh) into one answer per frame.
Frame i of the reversed run is frame N-1-i of the forward one. Where only one solved, that one; where both did, the one with
more inliers (the two agree to ~0.15 m where both track well; a disagreement is one of them carrying a wrong pose).
  python tools/dvr/udgp_merge_track.py <forward.jsonl> <reversed.jsonl> <out.jsonl>"""
import json, sys

fw, rv, out = sys.argv[1:4]
F = {r["i"]: r for r in map(json.loads, open(fw))}
R = list(map(json.loads, open(rv)))
N = max(F) + 1
if not R or max(r["i"] for r in R) + 1 != N: sys.exit(f"{fw} ends at frame {N - 1}, {rv} at {max((r['i'] for r in R), default=-1)}: a run stopped early")
B = {}
for r in R:
    if "pos" in r: B[N - 1 - r["i"]] = dict(r, i=N - 1 - r["i"], how="rev-" + r.get("how", ""))
n = dict(fw=0, rev=0, both=0, none=0)
with open(out, "w") as o:
    for i in range(N):
        a, b = F.get(i), B.get(i)
        a = a if a and "pos" in a else None
        if a and b: r = a if a["inl"] >= b["inl"] else b; n["both"] += 1
        elif a: r = a; n["fw"] += 1
        elif b: r = b; n["rev"] += 1
        else: r = dict(i=i, how="none"); n["none"] += 1
        o.write(json.dumps(r) + "\n")
print(out, n, f"solved {(N - n['none']) / N:.1%}")
