"""a Speedhive session's official laps placed on the day's heats: each participant goes to the heat record (live/relay/<day>)
whose cells name them and whose time holds their passes; the official time of day is put on this box's clock by the
difference most passes share with the decoder's own (the log, live/mylaps), to 20 ms: the right one lines up every pass to the
millisecond, a wrong one only a few by chance. (The median of each pass's nearest pass of the registered transponder was 15 s
off in most sessions of JDL R7 10/25: the laps on the replay pages ran apart from the drones.) Writes
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
dec = np.array(sorted(p["rtc"] - off for p in ps))
for r in rows:
    j = get(f"/sessions/{sid}/lapdata/{r['position']}/laps"); info = j["lapDataInfo"]["participantInfo"]; laps = j["laps"]
    if not laps: continue
    tod = [datetime.datetime.fromisoformat(l["timeOfDay"]).timestamp() * 1000 for l in laps]
    for t in tod:                                       # every decoder pass within a minute, whoever's (the registered transponder may not be the one flown)
        off_tod += list(dec[(dec > t - 60000) & (dec < t + 60000)] - t)
    per.append(dict(name=info["name"], tod=tod, laps=[sec(l["lapTime"]) for l in laps], driven=j["lapDataInfo"].get("lapsDriven") or len(laps), best=r.get("bestTime")))
shift, agree = 0.0, 0
if off_tod:
    d = np.array(off_tod); b = np.round(d / 20).astype(int); v, n = np.unique(b, return_counts=True)
    near = np.array([n[(v >= x - 1) & (v <= x + 1)].sum() for x in v]); x = v[np.argmax(near)]   # a pass's difference may fall either side of a bin edge
    shift = float(np.median(d[np.abs(b - x) <= 1])); agree = int(near.max())
print(f"{len(per)} participants; official time of day + {shift:.0f} ms = this box's clock ({agree} of {sum(len(p['tod']) for p in per)} passes agree)")
res, miss = {}, []
for p in per:
    jp = " ".join(p["name"].split()[:2]); ends = [t + shift for t in p["tod"]]; hs = ends[0] - p["laps"][0] * 1000
    cands = [(h, c) for h in heats for c, n in h["cells"].items() if n and (n.replace("桝", "枡") == jp.replace("桝", "枡") or (p["name"].startswith("Thomas") and n.startswith("Thomas"))) and h["start"] - 10000 <= hs <= h["end"] + 10000]
    if not cands: miss.append(p["name"]); continue
    h, c = max(cands, key=lambda hc: min(hc[0]["end"], ends[-1]) - max(hc[0]["start"], hs))
    res.setdefault(h["id"], {})[c] = dict(name=jp, laps=p["laps"], ends=[round(e) for e in ends], holeshot=round(hs), laps_driven=p["driven"], best=p["best"])
json.dump(dict(session=sid, shift_ms=round(shift), heats=res), open(out, "w"), ensure_ascii=False, indent=1)
print(f"placed {sum(len(v) for v in res.values())} in {len(res)} heats; not placed: {miss}")
