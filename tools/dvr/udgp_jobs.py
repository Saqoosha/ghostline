"""UDGP 2026 R3: one tracking job per HDZero feed (localize's slots.json), straight from the original ZowieBox file:
which file, where the heat's recording starts in it, how long, which cell, which lens (localize.py's trial picked it).
  python3 tools/dvr/udgp_jobs.py <build/udgp-2026-r3> <EventVRX dir> [pilot ...]
writes <build>/track/jobs.json and <build>/track/sources.txt (the original files the jobs need). Pilots named on the
command line (the analog ones, never localized) take every heat they flew instead; udgp_feed.sh picks the lens anyway."""
import datetime, json, os, sys

B, SRC, PILOTS = sys.argv[1], sys.argv[2], set(sys.argv[3:])
JST = datetime.timezone(datetime.timedelta(hours=9))
heats = {h["id"]: h for h in map(json.loads, open(f"{B}/rec/heats-20261115.jsonl"))}
if PILOTS:
    slots = {f"h{h['id']:02d}_s{n}": dict(heat=h["id"], slot=int(n), pilot=p)
             for h in heats.values() if h.get("rec") for n, p in h["names"].items() if p in PILOTS}
else:
    slots = json.load(open(f"{B}/locall/slots.json"))
jobs, srcs = [], set()
for k, v in sorted(slots.items()):
    h = heats[v["heat"]]; rec = json.load(open(f"{B}/rec/{h['rec']}.json"))
    t = datetime.datetime.strptime(rec["source"][:15], "%Y%m%d-%H%M%S").replace(tzinfo=JST).timestamp()
    lf = f"{B}/locall/loc/{k}.json"
    lens = json.load(open(lf))["camera"].get("lens", "upgrade") if os.path.exists(lf) else "trial"
    jobs.append(dict(name=k, src=rec["source"], start=round(rec["start"] - t, 3), dur=round(rec["frames"] / rec["fps"], 3),
                     slot=v["slot"], lens=lens, pilot=v["pilot"]))
    srcs.add(rec["source"])
os.makedirs(f"{B}/track", exist_ok=True)
json.dump(jobs, open(f"{B}/track/jobs.json", "w"), ensure_ascii=False, indent=0)
open(f"{B}/track/sources.txt", "w").write("\n".join(sorted(srcs)) + "\n")
size = sum(os.path.getsize(os.path.join(SRC, s)) for s in srcs)
print(len(jobs), "feeds,", len(srcs), "source files,", round(size / 1e9, 1), "GB,", sum(j["lens"] == "stock" for j in jobs), "on the stock lens")
