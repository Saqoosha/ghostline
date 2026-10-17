"""Official laps of a bracket event (a Main) from LiveFPV: the results page of the event lists "Race N: <class>" rows with
their race result ids; each result page embeds racerLaps in JS (as for the qualifiers, livefpv_laps.py).
The bare results page shows the newest event, so save a Main while it is the newest, or pass the event's page.
usage: livefpv_bracket.py <results page URL or saved .html> <out.json>"""
import json, os, re, sys, urllib.request

BASE = "https://mgp.livefpv.com/results/"
get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})).read().decode("utf-8", "replace")
src, out_path = sys.argv[1], sys.argv[2]
idx = get(src) if src.startswith("http") else open(src, encoding="utf-8", errors="replace").read()
title = (re.findall(r"<title>([^<]+)</title>", idx) or [""])[0]
event = (re.findall(r"Championships? ([A-Z]-Main)", title) or ["?"])[0]
rows = re.findall(r"view_race_result&(?:amp;)?id=(\d+)\"[^>]*>\s*Race (\d+): ([^<]+)</a>\s*</t[dh]>\s*<td>([^<]+)</td>", idx, re.S)
if not rows:                                             # the layout of the row differs between pages: a looser pass
    rows = [(rid, n, c, d) for rid, n, c, d in re.findall(r"view_race_result&(?:amp;)?id=(\d+).*?Race (\d+): ([^<]+)<.*?<td>([^<]+)</td>", idx, re.S)]
out = []
for rid, n, cls, done in rows:
    s = get(f"{BASE}?p=view_race_result&id={rid}")
    pilots = []
    for blk in re.split(r"racerLaps\[\d+\] = \{", s)[1:]:
        name = re.search(r"'driverName' : '([^']*)'", blk).group(1)
        laps = [(int(a), float(b)) for a, b in re.findall(r"'lapNum' : '(\d+)',\s*'pos' : '\d*',\s*'time' : '([\d.]+)'", blk.split("positionData")[0])]
        pilots.append({"pilot": name, "laps": laps})
    out.append({"event": event, "race": int(n), "class": cls.strip(), "id": rid, "completed": done.strip(), "pilots": pilots})
    print(event, n, done.strip(), ", ".join(f"{p['pilot']} {[t for _, t in p['laps']]}" for p in pilots), flush=True)
if os.path.isdir(out_path):                              # a folder: one file per event, named by it (livefpv_d_main.json)
    out_path = os.path.join(out_path, f"livefpv_{event.lower().replace('-', '_')}.json")
if not out:                                              # never replace a good file with an empty scrape
    sys.exit(f"no races found in {src}; {out_path} left as it was")
if os.path.exists(out_path) and len(json.load(open(out_path))) > len(out):
    sys.exit(f"{out_path} already holds more races than this scrape; left as it was")
json.dump(sorted(out, key=lambda r: r["race"]), open(out_path, "w"), indent=1)
print(f"{event}: {len(out)} races -> {out_path}")
