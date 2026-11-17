"""UDGP 2026 R3: RotorHazard's laps into the heat pages, as JDL's relay writes them for its replays (viewer/src/heat.ts
lapRows / hsOf, aud/progress.ts): race.json "official" per heat, and heats.json "closed" (the day is over: oldest first,
no live block) with "sessions" (Race 1 / Race 2, from each class's first heat).
  python3 tools/dvr/udgp_official.py <RotorHazard .db> <heats-<date>.jsonl> <out dir with heats.json and heats/>
A pilot's first pass of the gate is the holeshot, the passes after it end the laps (deleted laps left out); RH's
lap_time_stamp is ms from the race's start, its start_time_formatted is the heats file's "start"."""
import glob, json, os, sqlite3, sys

db, heats_f, out = sys.argv[1:4]
con = sqlite3.connect(db)
heats = {h["id"]: h for h in map(json.loads, open(heats_f))}
n = 0
for f in sorted(glob.glob(f"{out}/heats/*-h*/race.json")):
    rid = int(os.path.basename(os.path.dirname(f)).rsplit("-h", 1)[1]); h = heats[rid]; t0 = h["start"] * 1000
    cells = {}
    for node, in con.execute("select distinct node_index from saved_pilot_race where race_id=?", (rid,)):
        stamps = [s for s, in con.execute("select lap_time_stamp from saved_race_lap where race_id=? and node_index=? and deleted=0 "
                                          "order by lap_time_stamp", (rid, node))]
        name = h["names"].get(str(node))
        if not name or not stamps: continue
        laps = [round((b - a) / 1000, 3) for a, b in zip(stamps, stamps[1:])]
        cells[f"s{node}"] = dict(name=name, laps=laps, ends=[round(t0 + s) for s in stamps[1:]], holeshot=round(t0 + stamps[0]),
                                 laps_driven=len(laps), best=f"{min(laps):.3f}" if laps else None)
    race = json.load(open(f)); race["official"] = dict(session=str(rid), cells=cells)
    json.dump(race, open(f, "w"), ensure_ascii=False); n += 1
lst = json.load(open(f"{out}/heats.json"))
first = {}
for h in lst["heats"]:
    s = " ".join(h["cls"].split("-")[0].split()[:2]) if h.get("cls") else ""      # "Race 2-Quarter Final" -> "Race 2"
    if s: first[s] = min(first.get(s, h["start"]), h["start"])
lst["closed"] = True
lst["sessions"] = [dict(name=k, **{"from": v}) for k, v in sorted(first.items(), key=lambda kv: kv[1])]
json.dump(lst, open(f"{out}/heats.json", "w"), ensure_ascii=False)
print(n, "race.json with official laps;", "sessions", lst["sessions"])
