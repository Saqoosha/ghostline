"""a Speedhive session's official laps placed on the day's heats: each participant goes to the heat record (live/relay/<day>)
whose cells name them and whose time holds their passes; the official time of day is put on this box's clock by the
median difference to the decoder's own passes (the log, live/mylaps) of the participant's transponder. Writes
{heat id: {cell: {name, laps [s], ends [ms], holeshot ms, laps_driven, best}}} as JSON.
env DAY (YYYYMMDD of the decoder log and the heat folder) and RELAY (the heat records: live/relay, or the night's copy of the
published list when rows were joined or renamed). python official_laps.py <session id> <out.json>"""
import json, glob, os, sys, urllib.request, datetime, numpy as np
B = "https://eventresults-api.speedhive.com/api/v0.2.3/eventresults"
get = lambda u: json.load(urllib.request.urlopen(urllib.request.Request(B + u, headers={"accept": "application/json", "user-agent": "ghostline/1"}), timeout=30))
sec = lambda s: sum(float(x) * 60 ** i for i, x in enumerate(reversed(s.split(":"))))
sid, out = sys.argv[1], sys.argv[2]
rows = get(f"/sessions/{sid}/classification")["rows"]
DAY, RELAY = os.environ.get("DAY", "20261024"), os.environ.get("RELAY", "live/relay")
ps = [json.loads(l) for l in open(f"live/mylaps/{DAY}.jsonl") if '"tx"' in l]; off = max(p["rtc"] - p["rx"] for p in ps)
heats = [json.load(open(f)) for f in glob.glob(f"{RELAY}/{DAY}/*.json")]
heats = [dict(id=h["id"], start=h["start"], end=h.get("end") or 9e15, cells=h.get("cells") or {}) for h in heats if h.get("cells")]
off_tod, per = [], []
for r in rows:
    j = get(f"/sessions/{sid}/lapdata/{r['position']}/laps"); info = j["lapDataInfo"]["participantInfo"]; laps = j["laps"]
    if not laps: continue
    tod = [datetime.datetime.fromisoformat(l["timeOfDay"]).timestamp() * 1000 for l in laps]
    tx = info.get("transponder"); mine = [p["rtc"] - off for p in ps if str(p["tx"]) == str(tx)]
    for t in tod:                                       # its own decoder pass within a minute (the registered transponder may not be the one it flew)
        if mine: d = min(mine, key=lambda m: abs(m - t)) - t; off_tod.append(d) if abs(d) < 60000 else None
    per.append(dict(name=info["name"], tod=tod, laps=[sec(l["lapTime"]) for l in laps], driven=j["lapDataInfo"].get("lapsDriven") or len(laps), best=r.get("bestTime")))
shift = float(np.median(off_tod)) if off_tod else 0.0
print(f"{len(per)} participants; official time of day + {shift:.0f} ms = this box's clock ({len(off_tod)} passes)")
res, miss = {}, []
for p in per:
    jp = " ".join(p["name"].split()[:2]); ends = [t + shift for t in p["tod"]]; hs = ends[0] - p["laps"][0] * 1000
    cands = [(h, c) for h in heats for c, n in h["cells"].items() if n and (n.replace("桝", "枡") == jp.replace("桝", "枡") or (p["name"].startswith("Thomas") and n.startswith("Thomas"))) and h["start"] - 10000 <= hs <= h["end"] + 10000]
    if not cands: miss.append(p["name"]); continue
    h, c = max(cands, key=lambda hc: min(hc[0]["end"], ends[-1]) - max(hc[0]["start"], hs))
    res.setdefault(h["id"], {})[c] = dict(name=jp, laps=p["laps"], ends=[round(e) for e in ends], holeshot=round(hs), laps_driven=p["driven"], best=p["best"])
json.dump(dict(session=sid, shift_ms=round(shift), heats=res), open(out, "w"), ensure_ascii=False, indent=1)
print(f"placed {sum(len(v) for v in res.values())} in {len(res)} heats; not placed: {miss}")
