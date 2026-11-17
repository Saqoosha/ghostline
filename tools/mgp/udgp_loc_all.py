"""UDGP 2026 R3: every heat's HDZero feeds (and ReinaStyle's, who flew both systems) cut at 30 fps and localized on one map.
Run on Studio in the work dir (rec/ from cut_races.py, env/ with pycolmap):
  env/bin/python tools/mgp/udgp_loc_all.py <map dir> <out dir> [heat ids...]     env J (12): localize workers, FPS (30)
writes <out>/frames/images/h<NN>_s<slot>/<ms>.jpg (+ masks) and <out>/loc/h<NN>_s<slot>.json, and <out>/slots.json
({folder: {heat, slot, pilot}}). A heat already localized is skipped."""
import json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

HDZERO = {"カロヤン", "shuntana", "くっすー", "シャーマツナブル", "yashiko", "KPro-FPV", "K-house", "a2c", "ReinaStyle❤FPV"}
mp, out = sys.argv[1], sys.argv[2]
only = {int(x) for x in sys.argv[3:]}
J, FPS = os.environ.get("J", "12"), os.environ.get("FPS", "30")
env = dict(os.environ, MEDIAN="3", RELOC_R="20", VIEW_W="15")
os.makedirs(f"{out}/loc", exist_ok=True)
slots = json.load(open(f"{out}/slots.json")) if os.path.exists(f"{out}/slots.json") else {}   # other heats' entries stay
todo, jobs = [], []
for l in open("rec/heats-20261115.jsonl"):
    h = json.loads(l)
    if not h.get("rec") or (only and h["id"] not in only): continue
    mine = {int(n): name for n, name in h["names"].items() if name in HDZERO}
    if not mine: continue
    race = f"h{h['id']:02d}"
    for n, name in mine.items(): slots[f"{race}_s{n}"] = dict(heat=h["id"], slot=n, pilot=name)
    folders = [f"{race}_s{n}" for n in sorted(mine)]
    if all(os.path.exists(f"{out}/loc/{f}.json") for f in folders): continue
    jobs.append((race, h["rec"], sorted(mine), folders))


def extract(job):
    race, rec, mine, folders = job
    if not all(os.path.isdir(f"{out}/frames/images/{f}") for f in folders):
        r = subprocess.run(["env/bin/python", "tools/mgp/extract.py", f"rec/{rec}.mp4", f"{out}/frames", "0", "600", FPS],
                           env=dict(os.environ, LAYOUT="4fill", SLOTS=",".join(map(str, mine)), RACE=race), capture_output=True, text=True)
        print(race, "extract", r.stdout.strip().splitlines()[-len(mine):] if r.stdout else r.stderr[-300:], flush=True)
    return [f for f in folders if os.path.isdir(f"{out}/frames/images/{f}") and not os.path.exists(f"{out}/loc/{f}.json")]


with ThreadPoolExecutor(4) as ex:
    for fs in ex.map(extract, jobs): todo += fs
json.dump(slots, open(f"{out}/slots.json", "w"), ensure_ascii=False, indent=1)
print("localize", len(todo), "feeds", flush=True)
if todo:
    subprocess.run(["env/bin/python", "tools/mgp/localize.py", mp, f"{out}/loc"] + [f"{out}/frames/images/{f}" for f in todo] + ["-j", J], env=env, check=True)
