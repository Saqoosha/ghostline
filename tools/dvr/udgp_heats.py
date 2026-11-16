"""UDGP 2026 R3: the audience replay's heats from localize.py's answers (tools/mgp/udgp_loc_all.py on Studio), without a relay.
  uv run --with numpy python tools/dvr/udgp_heats.py <rec dir> <loc dir> <slots.json> <out dir>
<rec dir>: cut_races.py's recordings (<YYYYMMDD>-h<NN>.mp4 / .json, heats-<date>.jsonl from RotorHazard).
Per heat: each localized feed's answers, map frame (z up, metres) turned to the page's frame (y up) as ply_to_web.py does,
jumps dropped, then night_heats.cut_heat() cuts the clips and writes <out>/heats/<id>/race.json. Also <out>/heats.json,
the list the event page reads (the relay writes it on a live day)."""
import datetime, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import night_heats as nh

rec_dir, loc_dir, slots_f, out = sys.argv[1:5]
TRACK = os.environ.get("TRACK", "")                             # a folder of rt_track.py answers, h<NN>_s<n>.jsonl: used over localize.py's
ONLY = {int(x) for x in os.environ.get("ONLY", "").split(",") if x}   # these heats alone (the listing then has only them)
R = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)       # page = R @ map (ply_to_web.py)
VMAX = 40.0                                                     # m/s: a racing whoop-class quad stays well under this
nh.GAP = 1.5                                                    # the HDZero feed breaks up indoors for up to a second
JST = datetime.timezone(datetime.timedelta(hours=9))
slots = json.load(open(slots_f))
os.makedirs(f"{out}/heats", exist_ok=True)
ans_dir = f"{out}/answers"


def clean(t, P):
    """drop answers that need more than VMAX to reach both neighbours (a lone wrong pose), and ones off the floor plan"""
    keep = np.ones(len(t), bool)
    for _ in range(3):
        i = np.flatnonzero(keep)
        if len(i) < 3: break
        v = np.linalg.norm(np.diff(P[i], axis=0), axis=1) / np.maximum(np.diff(t[i]), 1e-3)
        bad = np.r_[v[0] > VMAX, (v[:-1] > VMAX) & (v[1:] > VMAX), v[-1] > VMAX]
        if not bad.any(): break
        keep[i[bad]] = False
    keep &= (P[:, 2] > -1.0) & (P[:, 2] < 15.0)
    return keep


listing = []
for l in open(f"{rec_dir}/heats-20261115.jsonl"):
    h = json.loads(l)
    if not h.get("rec") or (ONLY and h["id"] not in ONLY): continue
    rec = dict(json.load(open(f"{rec_dir}/{h['rec']}.json")), mp4=f"{rec_dir}/{h['rec']}.mp4", stem=h["rec"])
    rec["size"] = rec.get("size") or [1920, 1080]
    race = f"h{h['id']:02d}"; os.makedirs(f"{ans_dir}/{rec['stem']}", exist_ok=True)
    for n in range(4):
        f, tf = f"{loc_dir}/{race}_s{n}.json", f"{TRACK}/{race}_s{n}.jsonl"
        if TRACK and os.path.exists(tf):                # rt_track.py's answers (frame i of the same recording, from its start)
            fr = [x for x in map(json.loads, open(tf)) if "pos" in x]
            t = np.array([x["i"] for x in fr]) / rec["fps"]; P = np.array([x["pos"] for x in fr])
        elif os.path.exists(f):
            fr = [x for x in json.load(open(f))["frames"] if x["ok"]]
            t = np.array([x["t_ms"] for x in fr]) / 1000.0; P = np.array([x["center"] for x in fr]) if fr else None
        else: continue
        if not fr: continue
        k = clean(t, P)
        with open(f"{ans_dir}/{rec['stem']}/s{n}.jsonl", "w") as o:
            for ti, p in zip(t[k], P[k] @ R.T):
                o.write(json.dumps(dict(i=int(round(ti * rec["fps"])), pos=[round(float(c), 3) for c in p])) + "\n")
        print(race, f"s{n}", slots.get(f"{race}_s{n}", {}).get("pilot"), f"{k.sum()}/{len(fr)} kept of {int(t[-1] - t[0])} s")
    hid = datetime.datetime.fromtimestamp(h["start"], JST).strftime("%H%M%S") + f"-h{h['id']}"
    hh = dict(id=hid, start=h["start"] * 1000, end=h["end"] * 1000, heat=str(h["id"]), cls=h["cls"], order=["s0", "s1", "s2", "s3"],
              cells={f"s{n}": nm for n, nm in h["names"].items()})
    d = nh.cut_heat(hh, [rec], f"{out}/heats", answers=ans_dir, shift=0)
    if d: listing.append(dict(id=hid, start=round(h["start"] * 1000), end=round(h["end"] * 1000), heat=str(h["id"]), cls=h["cls"],
                              pilots=[h["names"].get(str(n), "") for n in range(4)]))
listing.sort(key=lambda x: -x["start"])
if not ONLY: json.dump(dict(event="udgp-2026-r3", title="UDGP 2026 R3", live=None, heats=listing), open(f"{out}/heats.json", "w"), ensure_ascii=False)
print("heats", len(listing))
