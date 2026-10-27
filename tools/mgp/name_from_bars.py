"""Name a 4-up round's unnamed cells from the broadcast bar over each cell (pilot name, lap, time), read with macOS
Vision: for cells that laps and elimination left open (no laps, one lap that fits several pilots, two of each left).
Three frames across the round's clip window (race.json source; a recording holding several races gives
each its own window), the bar of each open cell (46 px over the 960x540 cell; 8-up 42 px over 640x360) OCR'd, and the closest still unplaced
pilot of the round taken when the match is clear (similarity >= MIN, 0.6, and that pilot claimed by no other cell).
usage (Mac, repo root; LAYOUT=8up for an 8-up qualifier): uv run --with ocrmac --with pillow python tools/mgp/name_from_bars.py <round>[=<recording>] ...
env: MGP_HOST, MGP_DIR (as make_mgp_race.py)"""
import difflib, glob, json, os, re, subprocess, sys, tempfile

from ocrmac import ocrmac

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOST, DIR = os.environ.get("MGP_HOST", "rt4090"), os.environ.get("MGP_DIR", "/mnt/data/mgp_studio")
MIN = float(os.environ.get("MIN", 0.6))
GRID, CW, CH, BAR = (3, 640, 360, 42) if os.environ.get("LAYOUT") == "8up" else (2, 960, 540, 46)   # LAYOUT=8up: an 8-up qualifier
laps = json.load(open(f"{ROOT}/data/mgp/livefpv_laps.json")) + json.load(open(f"{ROOT}/data/mgp/livefpv_finals.json"))
for arg in sys.argv[1:]:                                     # <round> or <round>=<recording> (no race.json yet)
    n = int(arg.split("=")[0]); off = next((x for x in laps if x["round"] == n), None)
    rj = f"{ROOT}/build/mgp/q{n:02d}/race.json"   # as round_mac.sh names them
    if os.path.exists(rj): src = json.load(open(rj))["source"]
    else:
        rec = arg.split("=")[1]; d = float(subprocess.check_output(["ssh", "-n", HOST, f"ffprobe -v error -show_entries format=duration -of csv=p=0 {DIR}/raw/{rec}"]))
        src = {"recording": rec, "clip_t0": 0.0, "clip_t1": d}
    q = f"{ROOT}/build/mgp/poses/q{n:02d}"
    named = {json.load(open(f))["pilot"]: json.load(open(f)).get("slot") for f in glob.glob(f"{q}/*.json")}
    cells = {int(os.path.basename(f)[1:-5]): f for f in glob.glob(f"{q}_slots/s*.json")}
    free_cells = [s for s in cells if s not in named.values() and json.load(open(cells[s]))["frames"]]
    free = [p["pilot"] for p in off["pilots"] if p["pilot"] not in named]
    if not free_cells or not free: continue
    picks = {}; texts = {s: "" for s in free_cells}
    with tempfile.TemporaryDirectory() as td:
        from PIL import Image
        for k, f in enumerate((0.25, 0.5, 0.75)):                 # the bar is not drawn all the time
            t = src["clip_t0"] + f * (src["clip_t1"] - src["clip_t0"]); png = f"{td}/f{k}.png"
            subprocess.run(["ssh", "-n", HOST, f"ffmpeg -v error -y -ss {t:.2f} -i {DIR}/raw/{src['recording']} -frames:v 1 -f image2 -vcodec png -"],
                           stdout=open(png, "wb"), check=True)
            im = Image.open(png)
            for s in free_cells:
                r, c = divmod(s, GRID)
                bar = im.crop((c * CW, r * CH, (c + 1) * CW, r * CH + BAR)).resize((CW * 2, BAR * 2))
                texts[s] += " " + " ".join(w[0] for w in ocrmac.OCR(bar).recognize())
        for s in free_cells:
            text = re.sub(r"[^A-Z0-9_\-]", " ", texts[s].upper())
            best = max(((max((difflib.SequenceMatcher(None, p, tok).ratio() for tok in text.split()), default=0), p) for p in free), default=(0, None))
            picks[s] = (best, text.strip()[:40])
    claimed = [p for (r_, p), _ in picks.values() if r_ >= MIN]
    for s, ((r_, p), text) in picks.items():
        if r_ >= MIN and claimed.count(p) == 1:
            J = json.load(open(cells[s])); J["pilot"] = p; json.dump(J, open(f"{q}/{p}.json", "w"))
            print(n, f"s{s}", "->", p, f"({r_:.2f}, bar '{text}')")
        else:
            print(n, f"s{s}", "left", f"(best {p} {r_:.2f}, bar '{text}')")
