"""Official laps of a bracket event (a Main) from LiveFPV: the results page of the event lists "Race N: <class>" rows with
their race result ids; each result page embeds racerLaps in JS (as for the qualifiers, livefpv_laps.py).
The bare results page shows the newest event, so save a Main while it is the newest, or pass the event's page.
A Main whose event page is not known (the A Main: its result ids are linked from the bracket page) is read by its race
result ids instead, "ids:<first>-<last>:<event>"; those pages carry the race number but no completion time.
usage: livefpv_bracket.py <results page URL or saved .html | ids:7131418-7131431:A-Main> <out.json>"""
import json, os, re, sys, urllib.error, urllib.request

BASE = "https://mgp.livefpv.com/results/"
get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})).read().decode("utf-8", "replace")
src, out_path = sys.argv[1], sys.argv[2]
if src.startswith("ids:"):
    span, event = src[4:].split(":")
    a, b = map(int, span.split("-"))
    rows = [(str(i), "", "", "") for i in range(a, b + 1)]   # race number and class come from each result page
else:
    idx = get(src) if src.startswith("http") else open(src, encoding="utf-8", errors="replace").read()
    title = (re.findall(r"<title>([^<]+)</title>", idx) or [""])[0]
    event = (re.findall(r"Championships? ([A-Z]-Main)", title) or ["?"])[0]
    rows = re.findall(r"view_race_result&(?:amp;)?id=(\d+)\"[^>]*>\s*Race (\d+): ([^<]+)</a>\s*</t[dh]>\s*<td>([^<]+)</td>", idx, re.S)
    if not rows:                                         # the layout of the row differs between pages: a looser pass
        rows = [(rid, n, c, d) for rid, n, c, d in re.findall(r"view_race_result&(?:amp;)?id=(\d+).*?Race (\d+): ([^<]+)<.*?<td>([^<]+)</td>", idx, re.S)]
out = []
for rid, n, cls, done in rows:
    try:
        s = get(f"{BASE}?p=view_race_result&id={rid}")
    except urllib.error.HTTPError as e:
        print(f"race result {rid}: {e}", flush=True); continue
    if not n:                                            # ids: "Open Results [M1 Race #3]"
        m = re.search(r"Results \[M\d+ Race #(\d+)\]", s)
        if not m:
            print(f"race result {rid}: no race number, skipped", flush=True); continue
        n, cls = m.group(1), (re.findall(r"Championship :: ([^:<]*?) :: LiveFPV", s) or [""])[0]
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
