"""Summarise ocr_scan.py output: per race, per cell, the pilot name and the lap times shown last.
usage: ocr_summary.py <ocr_dir> <out.json> [pilot substring to highlight]"""
import glob, json, os, re, sys
from collections import Counter

d, out = sys.argv[1], sys.argv[2]
want = sys.argv[3].upper() if len(sys.argv) > 3 else None
lap_re = re.compile(r"^\d{1,3}\.\d{2}$")
races = []
for fn in sorted(glob.glob(f"{d}/*.json")):
    if fn.endswith("test.json"):
        continue
    D = json.load(open(fn))
    rn = Counter(m.group(1) for r in D["rows"] for c in r["clock"] for m in [re.search(r"RACE\s*(\d+)", c)] if m)
    cells = {}
    for s in map(str, range(8)):
        names, laps = Counter(), []
        for r in D["rows"]:
            toks = r["cells"].get(s, [])
            for tk in toks:
                m = re.match(r"^([A-Z0-9_.\-]{3,})(?:\s*LAP\s*\d+)?$", tk.strip())
                if m and not lap_re.match(tk) and not re.match(r"^\d+(ST|ND|RD|TH)$", m.group(1)):
                    names[m.group(1)] += 1
            t = [float(x) for x in toks if lap_re.match(x)]
            if len(t) >= len(laps):
                laps = t
        if names:
            cells[s] = {"pilot": names.most_common(1)[0][0], "laps": laps}
    races.append({"file": os.path.basename(fn)[:-5], "race": rn.most_common(1)[0][0] if rn else None, "cells": cells})
json.dump(races, open(out, "w"), indent=1)
for R in races:
    line = f"{R['file']} race {R['race']}: " + ", ".join(f"s{s} {c['pilot']} {c['laps']}" for s, c in R["cells"].items())
    if not want or want in line.upper():
        print(line)
