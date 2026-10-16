"""Official lap times from LiveFPV (mgp.livefpv.com) for every qualifier round: the results index
gives round -> race result id and completion time; each result page embeds racerLaps in JS.
usage: livefpv_laps.py <out.json>"""
import json, re, sys, urllib.request

BASE = "https://mgp.livefpv.com/results/"
get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})).read().decode("utf-8", "replace")
idx = get(BASE)
rounds = re.findall(r"<th>(Qualifier Round (\d+))</th>.*?view_race_result&id=(\d+).*?<td>([^<]+)</td>", idx, re.S)
out = []
for _, n, rid, done in rounds:
    s = get(f"{BASE}?p=view_race_result&id={rid}")
    pilots = []
    for blk in re.split(r"racerLaps\[\d+\] = \{", s)[1:]:
        name = re.search(r"'driverName' : '([^']*)'", blk).group(1)
        laps = [(int(a), float(b)) for a, b in re.findall(r"'lapNum' : '(\d+)',\s*'pos' : '\d*',\s*'time' : '([\d.]+)'", blk.split("positionData")[0])]
        pilots.append({"pilot": name, "laps": laps})
    out.append({"round": int(n), "id": rid, "completed": done.strip(), "pilots": pilots})
    print(n, done.strip(), ", ".join(f"{p['pilot']} {[t for _, t in p['laps']]}" for p in pilots), flush=True)
json.dump(sorted(out, key=lambda r: r["round"]), open(sys.argv[1], "w"), indent=1)
