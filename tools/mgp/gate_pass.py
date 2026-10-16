"""Did the path go through the gates? For every measured gate (gates_from_map.py's gates.json, page world frame) list
each crossing of the gate's plane near the opening: lateral offset from the centre, height of the path above the
opening's base, and whether that is inside the opening. A pass "beside" the gate shows as |lateral| > half_width.
usage: gate_pass.py <gates.json> <pose.json>...   (pose files as make_mgp_race.py reads them)"""
import json, sys
import numpy as np

G = json.load(open(sys.argv[1]))
gates = [g for g in G["checkpoints"] if g.get("half_width") and g.get("height") and g.get("width_dir") and g.get("confidence", 1) > 0]
for pf in sys.argv[2:]:
    J = json.load(open(pf)); F = J["frames"]
    t = np.array([f["t_ms"] for f in F]) / 1000; P = np.array([f["center"] for f in F])
    print(f"== {J.get('pilot', pf)}: {len(F)} frames, {t[0]:.1f}..{t[-1]:.1f} s")
    for g in gates:
        c, n, w = (np.array(g[k]) for k in ("centre", "normal", "width_dir"))
        n = n / np.linalg.norm(n); w = w / np.linalg.norm(w)
        s = (P - c) @ n
        out = []
        for i in np.nonzero(s[:-1] * s[1:] < 0)[0]:
            u = s[i] / (s[i] - s[i + 1]); X = P[i] * (1 - u) + P[i + 1] * u
            lat, h = float((X - c) @ w), float(X[1] - c[1])
            if abs(lat) < g["half_width"] + 3 and -1 < h < g["height"] + 3:
                ok = abs(lat) < g["half_width"] and 0 < h < g["height"]
                out.append(f"{t[i] + u * (t[i + 1] - t[i]):6.2f}s lat {lat:+5.2f} h {h:4.2f} {'ok' if ok else 'MISS'}")
        print(f"  gate {g['order']:>2} ({g.get('source', '')[:6]:6}) hw {g['half_width']:.2f} h {g['height']:.2f}: " + (" | ".join(out) if out else "no crossing"))
