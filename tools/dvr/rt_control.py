"""rt_control.py: the field box's control page. Serves rt_control.html and starts / stops rt_track.py on an NDI source
(a 2x2 grid, one pilot per cell), so the box needs no keyboard at the venue: pick the source the Event VRX sends, name
the cells, start (the frame size and rate are read off the source). While the tracker runs it reads its output (signal on / off per cell), its PUSH stream
(frames processed and solved per cell, the positions for the page's top view) and the GPU's power from nvidia-smi. The page also
shows the source's picture: its low-bandwidth stream, two JPEGs a second, received only while a page asks for it.
Beside all that, every NDI source is recorded while any cell of its grid shows a picture (rt_rec.py), tracker or not; /rec lists the files.
usage (mastenv, from the folder with rt_track.py, the maps and the engines): python rt_control.py      # http://<box>:8080, the top view alone at /obs
env: PORT (8080), PUSH (8765: the tracker's answers, also what viewer/live.html reads), ENG (eng_linux),
     REC, REC_* (rt_rec.py), CONF (~/.ghostline-control.json: the last start, shown again when the page opens), VIEW (~/.ghostline-view.json: the trails' delay and smoothing),
     CAM (~/.ghostline-cam.json: the camera of the 3D page that rt_ndiview.py sends),
     OUTCONF (~/.ghostline-out.json: what rt_ndiview.py sends - NDI, SRT, both or nothing - and at what size and rate),
     PILOTS_URL (unset: no names; the venue heat manager's current_pilots, e.g. http://192.168.3.20:8001/current_pilots at FDF),
     RELAY_BASE, RELAY_EVENT (both set and ~/.config/ghostline/key there: the answers go out to the audience pages,
     RELAY_BASE/RELAY_EVENT/, through rt_relay.py; it reads PUSH whether or not a tracker runs, and keeps heats in live/relay/)
Between heats (all cells without a picture for GROW_QUIET s) it grows the map with the frames the tracker kept in the last
GROW_WINDOW s (rt_map_grow.py: the day's light, docs/realtime-architecture.html 12) and the tracker swaps it in without stopping;
the page can grow now or go back to the scan alone. Needs "描いて照合" (RENDER): the new keyframes are matched beside the render.
routes beside the page: /api/state, /rec (recordings), /obs (top view for OBS), /view/ (the 3D live page, viewer/live.html built with VITE_BASE=/view/,
     and its data folders in view/), /api/pilots (the pilot per cell, from the heat manager), /api/cam (the sent picture's camera; POST sets it; /api/cam/events pushes each change), /api/out (rt_ndiview.py's output; POST sets it), /obs-setup (how to receive it in OBS), /api/addr (this box's LAN addresses)
No login: whoever reaches the port can start and stop the tracker. Keep it on the venue LAN / Tailscale."""
import collections, glob, http.server, json, mimetypes, os, re, signal, subprocess, sys, threading, time, traceback, urllib.parse, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); E = os.environ.get
PORT, PUSH, ENG, CONF = int(E("PORT", 8080)), int(E("PUSH", 8765)), E("ENG", "eng_linux"), os.path.expanduser(E("CONF", "~/.ghostline-control.json"))
CAM = os.path.expanduser(E("CAM", "~/.ghostline-cam.json"))     # the sent picture's camera (/view/?ndi=1 gets it from /api/cam/events)
OUTCONF = os.path.expanduser(E("OUTCONF", "~/.ghostline-out.json"))   # rt_ndiview.py reads it, and starts again when it changes
VIEW = os.path.expanduser(E("VIEW", "~/.ghostline-view.json"))   # how the trails are drawn (delay, smoothing): changed any time, read by every page
CELLS = ("tl", "tr", "bl", "br")                    # the grid's cells, row by row
# rt_track.py's knobs the page may set in "extra". Not any variable: the page has no login, and LD_PRELOAD is a variable too
KNOBS = set("NKF MIN_INL LOST RELOC_TOPK RELOC_MIN TOPK MAPK BATCH FRESH PIPE BA BA_PX BA_ACC BA_ALPHA RENDER RNEAR RFALL RCLIP ROPA RBATCH REDGE "
            "REACH NEAR FLOW FLOW_EVERY SIGNAL INFO PNP PL_DYN".split())
lock = threading.Lock(); gpu = {}
HIST = 300; hist = collections.deque(maxlen=HIST)   # one sample a second, for the page's sparklines: it shows the last 5 minutes whenever it is opened
KF_DIR, MAP_PTR = "live/kf", "live/map.txt"         # the frames the tracker keeps, and the file that names the map it should use (MAPWATCH)
GROW_WINDOW, GROW_QUIET = int(E("GROW_WINDOW", 1800)), int(E("GROW_QUIET", 30))   # s: only the heats just before (older light hurt); a heat is over after this long without a picture
GROW_KF = int(E("GROW_KF", 300))                    # s: a place gets a fresh frame once its last is this old; the grow keeps the newest per place
G = dict(phase="idle", last=None, error=None, heat=False, quiet=None, using=None)   # growing the map between heats; using: the map the tracker says it swapped to
S = dict(phase="stopped", proc=None, since=None, exit=None, ndi=None, input=None, config=None, cells={}, log=collections.deque(maxlen=400))

try:                                                # the same finder rt_track.py uses; it keeps its list fresh on a thread
    from cyndilib.finder import Finder
    finder = Finder(); finder.open()
except Exception as e: finder = None; print("no NDI finder:", e, flush=True)

class Preview:
    """The picture behind the page's grid: the source's low-bandwidth stream (NDI senders always carry one, about 640 wide), a JPEG
    twice a second. It runs only while a page keeps asking, and beside the tracker's own receiver, not through it."""
    def __init__(self): self.name = self.have = self.jpg = None; self.asked = 0; threading.Thread(target=self.run, daemon=True).start()
    def get(self, name): self.name, self.asked = name, time.time(); return self.jpg if self.have == name else None
    def wanted(self, name): return self.name == name and time.time() - self.asked < 8
    def run(self):
        import cv2, numpy as np
        from rt_rec import receiver
        rx = vf = None                              # one receiver for good, reconnected per source: a dropped one can freeze the process (rt_rec.py)
        while True:
            name = self.name
            src = next((s for s in finder.iter_sources() if name in s.name), None) if finder and name and self.wanted(name) else None
            if src is None: self.jpg = None; time.sleep(1); continue
            if rx is None: rx, vf = receiver(False)
            old = vf.get_timestamp_posix() if vf.xres else None   # the last source's last frame, still held
            rx.set_source(src)
            try:
                while self.wanted(name):
                    rx.frame_sync.capture_video()
                    if vf.xres and vf.get_timestamp_posix() != old:
                        w, h = vf.get_resolution(); img = vf.get_array().reshape(h, w, 4)[..., :3]
                        img = cv2.resize(img, (640, round(h * 640 / w)), interpolation=cv2.INTER_AREA) if w > 640 else np.ascontiguousarray(img)
                        self.jpg, self.have = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])[1].tobytes(), name
                    time.sleep(0.5)
            except Exception as e: print("preview:", e, flush=True); time.sleep(2)
            finally: rx.disconnect(); self.jpg = None
preview = Preview()
from rt_rec import Recorders, FILE, REC, REC_DIR, REC_ENC
recorders = Recorders(finder)

courses = {}
def course(name, line=""):
    """the map's keyframes seen from above: their positions ([x east, z south] in metres, thinned to 700-1,400) show where the course
    runs; box is the ground the page draws (x0, z0, width, height), ytop the height the scan is looked down on from. With a course
    line the box keeps its size and is moved to have the line's middle (the centre of its bounds) in the middle."""
    if name not in options()["maps"]: raise ValueError("地図が見つからない")
    if line in options()["tracks"]:                # an unknown line only loses the centring; /api/track reports it
        c, t = dict(course(name)), track(line)["pts"]
        if t:
            xs, zs = [p[0] for p in t], [p[1] for p in t]; w, h = c["box"][2:]
            c["box"] = [round((min(xs) + max(xs) - w) / 2, 1), round((min(zs) + max(zs) - h) / 2, 1), w, h]
        return c
    if name not in courses:
        import numpy as np
        pos = np.load(f"{HERE}/{name}")["pos"]; y = pos[:, 1]; pad = 4
        # the smallest box around the flight paths: the middle 99% of the positions (a few stray ones widened it by 13 and 16 m) and 4 m around
        (x0, x1), (z0, z1) = np.percentile(pos[:, 0], [0.5, 99.5]), np.percentile(pos[:, 2], [0.5, 99.5])
        courses[name] = dict(pts=[[round(float(a), 2), round(float(b), 2)] for a, _, b in pos[::max(1, len(pos) // 700)]],
                             box=[round(float(v), 1) for v in (x0 - pad, z0 - pad, x1 - x0 + 2 * pad, z1 - z0 + 2 * pad)],
                             ytop=round(float(np.median(y)) + 30, 1))
    return courses[name]

def track(name):
    """a race.json's course line (make_race.py: the anchor pilot's laps averaged, 1,600 points along a lap, about 25 cm apart on the FDF course; the page closes it) seen from above, and its
    start / finish gate as the segment across it"""
    if name not in options()["tracks"]: raise ValueError("コースの線が見つからない")
    r = json.load(open(f"{HERE}/{name}")); t = r.get("track") or []; g = r.get("gate")
    pts = [[round(p[0], 2), round(p[2], 2)] for p in t]
    gate = [[round(g["centre"][0] + k * g["half_width"] * g["width_dir"][0], 2), round(g["centre"][2] + k * g["half_width"] * g["width_dir"][2], 2)] for k in (-1, 1)] if g else None
    return dict(pts=pts, gate=gate)

rendering, failed = set(), {}                    # failed: file -> when; tried again after 2 minutes (a render during a heat may hit a full GPU)
def topview(name, scene, line=""):
    """the scan from straight above over the course's box (rt_topview.py, a few seconds, once per scene, map and course line; the file is kept
    in live/). Returns the file, None while it is being rendered, and raises for 2 minutes after a render failed."""
    c = course(name, line)
    if scene not in options()["scenes"]: raise ValueError("シーンが見つからない")
    stem = lambda f: re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.splitext(os.path.basename(f))[0]); out = f"{HERE}/live/topview-{stem(scene)}-{stem(name)}-{'_'.join(str(round(v)) for v in c['box'])}.jpg"
    if os.path.exists(out): return out
    if time.time() - failed.get(out, 0) < 120: raise ValueError("真上からの絵を描けなかった（ログ参照。2 分後にもう一度試す）")
    with lock:
        if out in rendering: return None
        rendering.add(out)
    def run():
        os.makedirs(HERE + "/live", exist_ok=True); part = out[:-4] + ".part.jpg"
        r = subprocess.run([sys.executable, "rt_topview.py", scene, part, *map(str, c["box"]), str(c["ytop"]), f"{min(16, 1600 / c['box'][2]):.2f}"], cwd=HERE, capture_output=True, text=True,
                           env=dict(os.environ, PATH=f"{os.path.dirname(sys.executable)}:/usr/local/cuda-12.9/bin:{os.environ.get('PATH', '')}"))
        try:
            if r.returncode == 0 and os.path.exists(part): os.replace(part, out)
            else: failed[out] = time.time(); print("topview failed:", r.stderr[-400:], flush=True)
        finally: rendering.discard(out)
    threading.Thread(target=run, daemon=True).start()

_opt = [0.0, None]
PILOTS_URL = E("PILOTS_URL", "")                   # the venue's heat manager: [name per channel ..., heat, class]; a venue address, so no default
_pilots = [0.0, {}, None]                           # when asked, the last good answer, the last failure (printed once)
def cell_name(x): return re.sub(r"[^A-Za-z0-9_-]", "", str(x.get("name") or ""))[:24] or str(x.get("cell"))   # as the tracker's stream names
def pilots():
    """the current heat's pilot per cell, from the heat manager (asked at most every 3 s). Its list has a name per channel, every
    channel, an empty one "" (FDF 2026-10-17), in the order of the config's cells (R2, F1, R4, R5 there), then the heat and the class.
    A failed ask keeps the last good answer, marked stale; {} when there is none, and the page shows the cell names alone."""
    if PILOTS_URL and time.time() - _pilots[0] > 3:
        _pilots[0] = time.time()
        try:
            with urllib.request.urlopen(PILOTS_URL, timeout=1) as f: r = json.loads(f.read(65536))
            cells = [cell_name(x) for x in ((S["config"] or {}).get("cells") or json.load(open(CONF)).get("cells", []))]
            if not isinstance(r, list) or len(r) < len(cells): raise ValueError(f"expected [{len(cells)} names, heat, class], got {str(r)[:80]}")
            _pilots[1] = dict(cells={c: str(n) if n else "" for c, n in zip(cells, r)}, heat=r[len(cells)] if len(r) > len(cells) else None,
                              cls=r[len(cells) + 1] if len(r) > len(cells) + 1 else None)
            if _pilots[2]: print("pilots: the heat manager answers again", flush=True); _pilots[2] = None
        except Exception as e:                      # unreachable (a venue LAN changes), or an answer of another shape
            if str(e) != _pilots[2]: print(f"pilots: {PILOTS_URL}: {e}", flush=True); _pilots[2] = str(e)
            if _pilots[1]: _pilots[1] = dict(_pilots[1], stale=True)
    return _pilots[1]

def cell_order():                                   # the cells' names in grid order (tl, tr, bl, br): the audience pages colour by place
    conf = S["config"] or json.load(open(CONF))
    by = {x.get("cell"): cell_name(x) for x in conf.get("cells", []) if isinstance(x, dict)}
    return [by.get(c, c) for c in CELLS]

relay = relay_off = None                            # relay_off: why a configured relay is not running, shown on the page
if E("RELAY_BASE") or E("RELAY_EVENT"):
    from rt_relay import Relay, read_key
    try:
        if not (E("RELAY_BASE") and E("RELAY_EVENT")): raise ValueError("RELAY_BASE と RELAY_EVENT の両方が要る")
        relay = Relay(f"http://127.0.0.1:{PUSH}/", E("RELAY_BASE"), E("RELAY_EVENT"), read_key(), pilots=lambda: pilots(), out_dir=HERE + "/live/relay", order=cell_order,
                      rec_dir=REC_DIR if REC else None, nvenc=REC_ENC == "h264_nvenc").start_threads()   # rec_dir: each heat's DVR, right after it
    except (OSError, ValueError) as e: relay_off = f"送信しない: {e}"; print("relay off:", e, flush=True)

def options():
    if time.time() - _opt[0] < 2 and _opt[1]: return _opt[1]   # every open page asks once a second: the globs and the finder are read at most every 2 s
    rel = lambda ps: sorted(os.path.relpath(p, HERE) for p in ps)
    try: src = sorted(finder.get_source_names()) if finder else []
    except Exception: src = []
    # the lenses the pilots may fly; offered together, the tracker picks per cell (LENS_* in rt_track.py). A cell starts on the first:
    # the upgrade lens, which most pilots fly (7 of 8 in the FDF semifinal)
    lenses = sorted(rel(glob.glob(HERE + "/cams/*.json")), key=lambda f: ("upgrade" not in f, f))
    _opt[:] = [time.time(), dict(maps=rel(glob.glob(HERE + "/map_*.npz")), cams=(["+".join(lenses)] if len(lenses) > 1 else []) + lenses + rel(glob.glob(HERE + "/../*/dvr_pinhole.mp4.json")),
                                  scenes=sorted(glob.glob(os.path.expanduser("~/scenes/*.ply"))), tracks=rel(glob.glob(HERE + "/tracks/*.json")), sources=src)]   # tracks: race.json files (make_race.py), the course line
    return _opt[1]

def probe(name):
    """the source's full name, frame size and rate, read off its first frame: the tracker's clock runs on the rate, so it is not typed in"""
    from cyndilib.receiver import Receiver
    from cyndilib.video_frame import VideoFrameSync
    from cyndilib.wrapper.ndi_recv import RecvColorFormat, RecvBandwidth
    if finder is None: raise ValueError("NDI が使えない（cyndilib が無い）")
    t0 = time.time(); src = None
    while src is None and time.time() - t0 < 5: finder.wait(0.5); src = next((s for s in finder.iter_sources() if name in s.name), None)
    if src is None: raise ValueError(f"NDI の送り手「{name}」が見つからない")
    rx = Receiver(color_format=RecvColorFormat.BGRX_BGRA, bandwidth=RecvBandwidth.highest)   # highest: the low-bandwidth stream is a smaller picture
    vf = VideoFrameSync(); rx.frame_sync.set_video_frame(vf); rx.set_source(src); t0 = time.time()
    try:
        while not vf.xres and time.time() - t0 < 5: rx.frame_sync.capture_video(); time.sleep(0.01)
        if not vf.xres: raise ValueError(f"{src.name} から映像が来ない")
        (w, h), fps = vf.get_resolution(), float(vf.get_frame_rate())
    finally: rx.disconnect()
    return src.name, w, h, fps

def start(c):
    if not isinstance(c, dict) or not all(isinstance(x, dict) for x in c.get("cells", [])): raise ValueError("JSON の形がおかしい")
    o = options(); src = str(c.get("source", "")).strip()
    if not src or re.search(r"[,|]", src): raise ValueError("NDI の送り手の名前が要る（, と | は使えない）")
    if c.get("cam") not in o["cams"]: raise ValueError("カメラの定義が見つからない")
    if c.get("map") not in o["maps"]: raise ValueError("地図が見つからない")   # one map for every cell: a venue has one course
    if c.get("track") and c["track"] not in o["tracks"]: raise ValueError("コースの線が見つからない")
    on = [x for x in c.get("cells", []) if x.get("on")]
    if not on: raise ValueError("使うマスが 1 つも無い")
    names = [cell_name(x) for x in on]
    if len(set(names)) < len(names): raise ValueError("マスの名前が重なっている")
    with lock:
        if S["phase"] != "stopped": raise ValueError("もう動いている")
    full, w, h, fps = probe(src)
    if not (fps > 0 and w > 0 and h > 0): raise ValueError(f"{full} の大きさか fps が読めない（{w}x{h}、{fps:g} fps）")
    if w % 2 or h % 2: raise ValueError(f"{full} は {w}x{h}：2×2 に割れない")
    cap = min(fps, 30)                                # the Event VRX sends 60 fps; 30 is what the accuracy and power were measured at, and all the dot needs
    specs = []
    for x, n in zip(on, names):
        if x.get("cell") not in CELLS: raise ValueError(f"{n}: マスの指定がおかしい")
        k = CELLS.index(x["cell"]); specs.append(f"{c['map']},ndi://{src}|{k % 2 * (w // 2)}:{k // 2 * (h // 2)}:{w // 2}:{h // 2}|{c['cam']},live/{n}")
    env = dict(os.environ, PATH=f"{os.path.dirname(sys.executable)}:/usr/local/cuda-12.9/bin:{os.environ.get('PATH', '')}",
               SIGNAL="1", GRID=f"{w}x{h}", GRID_FPS=f"{cap:g}", PUSH=str(PUSH), **({"MAXFPS": f"{cap:g}"} if cap < fps else {}))
    for k, f in (("GLUE", "glue_mix.engine"), ("XFEAT", "xfeat_fp32.engine")):
        if os.path.exists(f"{HERE}/{ENG}/{f}"): env[k] = f"{ENG}/{f}"
    if c.get("render"):
        if c.get("scene") not in o["scenes"]: raise ValueError("描いて照合するにはシーン（.ply）が要る")
        env.update(RENDER="1", RCLIP="2", SCENE=c["scene"], RKF="1", KFOUT=KF_DIR, KF_WINDOW=str(GROW_KF), MAPWATCH=MAP_PTR)
    if c.get("ba"): env.update(BA="0.5", BA_ACC="30", BA_ALPHA="60")   # the outdoor setting, docs/realtime-tracking.ja.md
    for kv in str(c.get("extra", "")).split():
        k, _, v = kv.partition("=")
        if k not in KNOBS or not re.fullmatch(r"[A-Za-z0-9.]+", v): raise ValueError(f"追加の設定 {kv} は使えない")
        env[k] = v
    with lock:
        if S["phase"] != "stopped": raise ValueError("もう動いている")
        os.makedirs(HERE + "/live", exist_ok=True); point(c["map"])   # each start begins on the scan alone; the next heat grows it again
        made = f"{c['map']}|{c.get('scene')}"; tag = f"{HERE}/{KF_DIR}/made-with"   # frames posed on another map are in another world frame
        if os.path.isdir(f"{HERE}/{KF_DIR}") and (not os.path.exists(tag) or open(tag).read() != made):
            for f in glob.glob(f"{HERE}/{KF_DIR}/*.npz"): os.remove(f)
        os.makedirs(f"{HERE}/{KF_DIR}", exist_ok=True)
        with open(tag, "w") as f: f.write(made)
        G.update(last=None, error=None, heat=False, quiet=None, using=None)
        p = subprocess.Popen([sys.executable, "-u", "rt_track.py", *specs], cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        S.update(phase="loading", proc=p, since=time.time(), exit=None, ndi=None, config=c, input=f"{w}×{h} · {fps:g} fps" + (f" → {cap:g}" if cap < fps else ""),
                 cells={n: dict(cell=x["cell"], signal=None, lens=None, rows=collections.deque(maxlen=600), last=None, trail=collections.deque(maxlen=150)) for x, n in zip(on, names)})
        S["log"].clear(); S["log"].append("$ rt_track.py " + " ".join(specs))
    json.dump({k: c.get(k) for k in ("source", "map", "cam", "scene", "track", "render", "ba", "extra", "cells")}, open(CONF, "w"), ensure_ascii=False)
    threading.Thread(target=watch, args=(p,), daemon=True).start(); threading.Thread(target=answers, args=(p,), daemon=True).start()

def point(path):                                    # tell the tracker which map to use (it reads the file every 2 s)
    tmp = f"{HERE}/{MAP_PTR}.tmp"
    with open(tmp, "w") as f: f.write(path)
    os.replace(tmp, f"{HERE}/{MAP_PTR}")

def grow(reason):
    with lock:
        c = S["config"]
        if S["phase"] != "running" or not c: raise ValueError("tracker が動いていない")
        if not c.get("render"): raise ValueError("地図を育てるには「描いて照合」が要る")
        if G["phase"] == "growing": raise ValueError("いま育てている")
        G.update(phase="growing", error=None); run = S["since"]
    threading.Thread(target=run_grow, args=(c, reason, run), daemon=True).start()

def run_grow(c, reason, run):                       # run: the start it belongs to; a grow that outlives its tracker is thrown away
    out, t0 = time.strftime("live/map-grow-%Y%m%d-%H%M%S.npz"), time.time()
    try:
        for f in glob.glob(f"{HERE}/{KF_DIR}/*.npz") + glob.glob(f"{HERE}/live/*.part") + glob.glob(f"{HERE}/{KF_DIR}/*.part"):   # ~280 KB each, thousands a day; never used past the window (2x: a margin); .part: a killed write
            if t0 - os.path.getmtime(f) > 2 * GROW_WINDOW: os.remove(f)
        r = subprocess.run([sys.executable, "-u", "rt_map_grow.py", c["map"], c["scene"], out, KF_DIR], cwd=HERE, capture_output=True, text=True, timeout=600,
                           env=dict(os.environ, SINCE=str(GROW_WINDOW), PATH=f"{os.path.dirname(sys.executable)}:/usr/local/cuda-12.9/bin:{os.environ.get('PATH', '')}"))
        m = re.search(r"GROW-DONE \d+ \+ (\d+) real", r.stdout)
        if not m:
            tail = (r.stdout + r.stderr).strip().splitlines()
            raise RuntimeError("直前のフレームが無い" if "no frame to add" in r.stdout + r.stderr else (tail[-1][:200] if tail else f"終了コード {r.returncode}"))
        with lock:
            if S["since"] != run or S["phase"] == "stopped": G.update(phase="idle"); os.remove(f"{HERE}/{out}"); return
            point(out)
        for f in sorted(glob.glob(f"{HERE}/live/map-grow-*.npz"), key=os.path.getmtime)[:-2]: os.remove(f)   # the one in use and the one before
        with lock: G.update(phase="idle", last=dict(t=time.time(), n=int(m[1]), reason=reason, s=round(time.time() - t0), map=out))
        note(f"(地図を育てた：直前 {GROW_WINDOW // 60} 分の実フレーム {m[1]} 枚、{reason}、{time.time() - t0:.0f} 秒)")
    except Exception as e:
        with lock: G.update(phase="idle", error=str(e)[:200])
        note(f"(地図を育てられなかった：{e})")

def reset_map():
    with lock:
        c = S["config"]
        if S["phase"] == "stopped" or not c: raise ValueError("tracker が動いていない")
        if G["phase"] == "growing": raise ValueError("いま育てている")
        point(c["map"]); G.update(last=None, error=None)
    note("(地図をスキャンだけに戻した)")

def between_heats():                                # a heat is over when every cell has been without a picture for GROW_QUIET s
    while True:
      try:
        time.sleep(1); now = time.time(); go = False
        with lock:
            if S["phase"] == "running" and S["config"] and S["config"].get("render"):
                if any(c["signal"] for c in S["cells"].values()): G.update(heat=True, quiet=None)
                elif G["heat"]:
                    G["quiet"] = G["quiet"] or now
                    if now - G["quiet"] >= GROW_QUIET and G["phase"] == "idle": G.update(heat=False, quiet=None); go = True
        if go:
            try: grow("ヒートの後に自動")
            except ValueError: pass
      except Exception as e: note(f"(ヒートの区切りの見張りで例外：{e})")   # keep watching: the next heat still grows

def view():
    try: v = json.load(open(VIEW))
    except Exception: v = {}
    return dict(delay=v.get("delay", 250), smooth=v.get("smooth", 80))   # σ 80 ms: the wobble mostly gone, the line within ~0.2 m of the answers; 250 ms covers 2.5σ and the solve

def set_view(v):                                   # ms; the delay lets the smoothing see as far ahead of the drawn point as behind it
    if not isinstance(v, dict): raise ValueError("JSON の形がおかしい")
    d, m = v.get("delay"), v.get("smooth")
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (d, m)) or not (0 <= d <= 2000 and 0 <= m <= 1000): raise ValueError("遅らせる時間は 0〜2000 ms、なめらかさは 0〜1000 ms")
    tmp = f"{VIEW}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f: json.dump(dict(delay=round(d), smooth=round(m)), f)
    os.replace(tmp, VIEW)                # whole: /api/state reads it on other threads

def addrs():                                       # this box's IPv4 on the LAN, for the OBS page's URLs: not loopback, Tailscale (100.64/10) or docker
    try: out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=3).stdout
    except Exception: return []
    ips = [l.split()[3].split("/")[0] for l in out.splitlines() if len(l.split()) > 3 and not l.split()[1].startswith(("lo", "tailscale", "docker", "br-", "virbr"))]
    ips = [ip for ip in ips if not ip.startswith(("127.", "169.254."))]
    try: src = subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"], capture_output=True, text=True, timeout=3).stdout.split(" src ")[1].split()[0]   # the default route's own address first
    except Exception: src = None
    return sorted(ips, key=lambda ip: ip != src)

def cam():
    try: v = json.load(open(CAM))
    except Exception: v = {}
    if not isinstance(v, dict): v = {}                 # valid JSON but not an object (hand-edited): /api/state must not break on it
    return dict(mode=v.get("mode", "orbit"), zoom=v.get("zoom", 1))

cam_changed, cam_n = threading.Condition(), [0]    # /api/cam/events: each page waiting is told at once

def set_cam(v):                                    # mode: orbit, top, or p<output name> to follow that tracker output's pilot
    if not isinstance(v, dict): raise ValueError("JSON の形がおかしい")
    c = dict(cam(), **{k: v[k] for k in ("mode", "zoom") if k in v})
    if not (isinstance(c["mode"], str) and re.fullmatch(r"orbit|top|p[\w.-]{1,40}", c["mode"])): raise ValueError("カメラは orbit・top・p<出力の名前>")
    if not (isinstance(c["zoom"], (int, float)) and not isinstance(c["zoom"], bool) and 0.4 <= c["zoom"] <= 3): raise ValueError("ズームは 0.4〜3")
    tmp = f"{CAM}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f: json.dump(c, f)
    os.replace(tmp, CAM)
    with cam_changed: cam_n[0] += 1; cam_changed.notify_all()

OUTS, SIZES = ("ndi", "srt", "ndi,srt", "off"), ((1280, 720), (1920, 1080))
def out():
    try: v = json.load(open(OUTCONF))
    except Exception: v = {}
    if not isinstance(v, dict): v = {}
    return dict(out=v.get("out", "ndi"), w=v.get("w", 1920), h=v.get("h", 1080), fps=v.get("fps", 60))

def set_out(v):                                    # the sender's output: written whole, the sender starts again with it
    if not isinstance(v, dict): raise ValueError("JSON の形がおかしい")
    c = dict(out(), **{k: v[k] for k in ("out", "w", "h", "fps") if k in v})
    if c["out"] not in OUTS: raise ValueError("出力は ndi・srt・ndi,srt・off")
    if (c["w"], c["h"]) not in SIZES: raise ValueError("大きさは 1280x720 か 1920x1080")
    if c["fps"] not in (30, 60): raise ValueError("fps は 30 か 60")
    try:
        if json.load(open(OUTCONF)) == c: return       # unchanged: a write would restart the sender for nothing
    except Exception: pass                             # missing or broken: written
    tmp = f"{OUTCONF}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f: json.dump(c, f)
    os.replace(tmp, OUTCONF)

def watch(p):                                       # the tracker's output: the log, and the lines that say how it is doing
    # every run's whole output is kept too (live/tracker-<start>.log): the page shows only the last lines, and a run that went
    # wrong is understood from its lens decisions and summary afterwards. A failing write (a full disk) only stops the file:
    # the pipe must be drained or the tracker blocks on its next print
    try:
        try: f = open(time.strftime(f"{HERE}/live/tracker-%Y%m%d-%H%M%S.log"), "w")
        except OSError as e: f = None; note(f"(ログを残せない: {e})")
        for line in p.stdout:
            if f:
                try: f.write(time.strftime("%H:%M:%S ") + line); f.flush()
                except OSError as e: f = None; note(f"(ログを残せない: {e})")
            note(line.rstrip()[:300])
        if f: f.close()
    finally:
        code = p.wait()
        with lock: S.update(phase="stopped", proc=None, exit=code); S["log"].append(f"(終了、コード {code})")

def note(line):
    with lock:
            S["log"].append(line)
            if m := re.match(r"live/(.+): signal (on|off)$", line):
                if m[1] in S["cells"]: S["cells"][m[1]]["signal"] = m[2] == "on"
            elif m := re.match(r"live/(.+): lens (\S+) ", line):
                if m[1] in S["cells"]: S["cells"][m[1]]["lens"] = m[2]
            elif m := re.search(r": receiving (.+)$", line): S["ndi"] = m[1]
            elif line == "listening" and S["phase"] == "loading": S["phase"] = "running"
            elif m := re.match(r"map (\S+): (\d+ keyframes|.+)", line):   # the tracker's word on a swap: done, or why not
                if m[2].endswith("keyframes"): G.update(using=m[1], error=None)
                else: G["error"] = f"tracker が地図を読めなかった：{m[2][:150]}"
            elif line.startswith("KFOUT off"): G["error"] = f"tracker が実フレームを書けなくなった（{line[11:150]}）。次の開始まで育たない"

def answers(p):                                     # the PUSH stream: one row per processed frame, "pos" when it was solved
    while p.poll() is None:
        try:
            for raw in urllib.request.urlopen(f"http://127.0.0.1:{PUSH}/", timeout=86400):
                if not raw.startswith(b"data: "): continue
                r = json.loads(raw[6:]); now = time.time()
                with lock:
                    if c := S["cells"].get(r.get("stream")):
                        c["rows"].append((now, "pos" in r, r.get("lat")))
                        if "pos" in r:
                            c["last"] = dict(t=now, inl=r.get("inl"), lat=r.get("lat"), how=r.get("how"))
                            if not c["trail"] or now - c["trail"][-1][0] >= 0.1:   # ten points a second are enough for the top view
                                x, y, z = r["pos"]; c["trail"].append((now, round(x, 2), round(z, 2), round(y, 1)))
        except Exception: time.sleep(1)             # not listening yet, or the tracker went away

def stop():
    with lock:
        p = S["proc"]
        if p is None: raise ValueError("動いていない")
        S["phase"] = "stopping"
    p.send_signal(signal.SIGTERM)                   # the tracker writes its summary and files on TERM
    def wait():
        try: p.wait(40)
        except subprocess.TimeoutExpired: p.kill()
    threading.Thread(target=wait, daemon=True).start()

def poll_gpu():
    while True:
        try:
            w, ps, mem, temp, util = subprocess.run(["nvidia-smi", "--query-gpu=power.draw,pstate,memory.used,temperature.gpu,utilization.gpu", "--format=csv,noheader,nounits"],
                                                    capture_output=True, text=True, timeout=5).stdout.strip().split(", ")
            gpu.update(w=float(w), pstate=ps, mem=int(mem), temp=int(temp), util=int(util))
        except Exception: gpu.clear()
        time.sleep(1)

RAPL = "/sys/class/powercap/intel-rapl:0"         # the CPU package's energy counter: root only by default (CVE-2020-8694), the service's ExecStartPre opens it to its user's group
cpu = {}
def poll_cpu():                                    # W over each second from the counter's µJ (it wraps at max_energy_range_uj)
    prev, said = None, None
    while True:
        try:
            top, uj, t = int(open(RAPL + "/max_energy_range_uj").read()), int(open(RAPL + "/energy_uj").read()), time.monotonic()
            if prev: cpu["w"] = round((uj - prev[0]) % top / 1e6 / (t - prev[1]), 1)
            prev, said = (uj, t), None; cpu.pop("err", None)
        except Exception as e:                     # the reason goes to the page; the log says it once per kind of failure
            why = "読めない（ExecStartPre の chgrp / chmod）" if isinstance(e, PermissionError) else "RAPL が無い" if isinstance(e, FileNotFoundError) else type(e).__name__
            cpu["err"] = why; cpu.pop("w", None); prev = None   # in this order: a reader on another thread never sees neither
            if said != why: print(f"CPU power: {e} ({why})", flush=True); said = why
        time.sleep(1)

def latency(now):
    """ms from a frame's arrival to its position, the median over the last second's answers of every cell (None when there were none).
    The tracker's own "ms each" line is the time since its start over the passes made: it grows through every gap without a picture."""
    v = sorted(lat for c in S["cells"].values() for t, ok, lat in c["rows"] if ok and lat is not None and t > now - 1)
    return round(v[len(v) // 2], 1) if v else None

def sample():
    while True:
        time.sleep(1); now = time.time()
        with lock:
            on = S["phase"] != "stopped"
            hist.append(dict(w=gpu.get("w"), cw=cpu.get("w"), temp=gpu.get("temp"), lat=latency(now) if on else None,
                             cells={c["cell"]: round(sum(1 for t, ok, _ in c["rows"] if ok and t > now - 1), 1) for c in S["cells"].values()} if on else {}))

def state():
    now = time.time(); hz = lambda rows, solved: round(sum(1 for t, ok, _ in rows if t > now - 3 and (ok or not solved)) / 3, 1)
    with lock:
        cells = {n: dict(cell=c["cell"], signal=c["signal"], lens=c["lens"], hz=hz(c["rows"], False), solved=hz(c["rows"], True),
                         last=c["last"] and dict(c["last"], age=round(now - c["last"]["t"], 1)),
                         trail=[[x, z, round(now - t, 1)] for t, x, z, _ in c["trail"] if now - t < 12], alt=c["trail"][-1][3] if c["trail"] else None)
                 for n, c in S["cells"].items()}
        conf = S["config"]
        if conf is None and os.path.exists(CONF):
            try: conf = json.load(open(CONF))
            except Exception: conf = None
        return dict(phase=S["phase"], up=S["since"] and S["phase"] != "stopped" and round(now - S["since"]), exit=S["exit"], lat=latency(now) if S["phase"] != "stopped" else None, ndi=S["ndi"], input=S["input"],
                    config=conf, view=view(), cam=cam(), out=out(), grow=dict(phase=G["phase"], error=G["error"], last=G["last"] and dict(G["last"], age=round(now - G["last"]["t"]), swapped=G["using"] == G["last"]["map"])), cells=cells, rec=recorders.recording(), relay=relay.status() if relay else relay_off and dict(ok=False, age=None, event=E("RELAY_EVENT"), err=relay_off, pending=0), log=list(S["log"])[-80:], gpu=dict(gpu), cpu=dict(cpu), options=options(), push=PUSH,
                    hist=dict(n=HIST, w=[h["w"] for h in hist], cw=[h["cw"] for h in hist], temp=[h["temp"] for h in hist], lat=[h["lat"] for h in hist],
                              cells={c: [h["cells"].get(c) for h in hist] for c in CELLS}))

class Handler(http.server.BaseHTTPRequestHandler):
    timeout = 10                                    # a client that stops sending does not hold a thread for ever
    def send(self, code, body, ctype="application/json"):
        body = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application")) else "")); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
    def send_file(self, path, ctype):              # a recording, hours long: streamed, with ranges so the browser can seek
        size = os.path.getsize(path); a, b = 0, size - 1
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if m and (m[1] or m[2]):
            a, b = (int(m[1]), min(int(m[2] or b), b)) if m[1] else (max(0, size - int(m[2])), b)
            if a > b: self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.send_header("Content-Length", "0"); return self.end_headers()
            self.send_response(206); self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        else: self.send_response(200)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(b - a + 1)); self.send_header("Accept-Ranges", "bytes"); self.end_headers()
        with open(path, "rb") as f:
            f.seek(a); left = b - a + 1
            try:
                while left > 0 and (chunk := f.read(min(left, 1 << 20))): self.wfile.write(chunk); left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, TimeoutError): pass   # the browser seeks by dropping one range and asking for another
    def cam_events(self):                           # server-sent events: the camera now, then each change as it is made (a comment every 5 s keeps it open)
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store"); self.end_headers()
        seen = None
        try:
            while True:
                with cam_changed: cam_changed.wait_for(lambda: cam_n[0] != seen, 5)
                if cam_n[0] != seen: seen = cam_n[0]; self.wfile.write(f"data: {json.dumps(cam())}\n\n".encode())
                else: self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError): pass
    def do_GET(self):
        u = urllib.parse.urlsplit(self.path); q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/": self.send(200, open(HERE + "/rt_control.html", "rb").read(), "text/html")
        elif u.path == "/obs": self.send(200, open(HERE + "/rt_obs.html", "rb").read(), "text/html")   # the top view alone, for an OBS browser source
        elif u.path == "/rt_topview.js": self.send(200, open(HERE + "/rt_topview.js", "rb").read(), "text/javascript")
        elif u.path == "/api/state": self.send(200, state())
        elif u.path == "/obs-setup": self.send(200, open(HERE + "/rt_obs_setup.html", "rb").read(), "text/html")   # for the venue's OBS operator
        elif u.path == "/api/addr": self.send(200, addrs())
        elif u.path == "/api/cam": self.send(200, cam())
        elif u.path == "/api/cam/events": self.cam_events()
        elif u.path == "/api/out": self.send(200, out())
        elif u.path == "/api/pilots": self.send(200, pilots())   # the 3D page's name tags
        elif u.path == "/rec": self.send(200, open(HERE + "/rt_rec.html", "rb").read(), "text/html")   # the recordings
        elif u.path == "/api/rec": self.send(200, recorders.state())
        elif u.path.startswith("/rec/"):
            name = u.path[5:]
            if not FILE.fullmatch(name) or not os.path.exists(f"{REC_DIR}/{name}"): return self.send(404, dict(error="not found"))
            self.send_file(f"{REC_DIR}/{name}", "video/mp4")
        elif u.path == "/view" or u.path.startswith("/view/"):   # the 3D page: the viewer's build (VITE_BASE=/view/) and its data folders, in view/
            rel = urllib.parse.unquote(u.path[6:])
            if not rel and "data" in q:                  # a folder asked for: the query as it is (?ndi=1 and the like too)
                self.send_response(302); self.send_header("Location", "/view/live.html?" + u.query); self.send_header("Content-Length", "0"); return self.end_headers()
            if not rel:                                  # the newest scene folder (view/<name>/index.json)
                ix = sorted(glob.glob(f"{HERE}/view/*/index.json"), key=lambda f: os.stat(f).st_mtime if os.path.exists(f) else 0)
                if not ix: return self.send(404, dict(error="view/ に index.json のあるフォルダが無い"))
                self.send_response(302); self.send_header("Location", "/view/live.html?data=" + urllib.parse.quote(os.path.basename(os.path.dirname(ix[-1]))) + (u.query and "&" + u.query))   # ?ndi=1 and the like carried over
                self.send_header("Content-Length", "0"); return self.end_headers()
            if "\0" in rel: return self.send(404, dict(error="not found"))
            path = os.path.realpath(f"{HERE}/view/{rel}")   # symlinks out of view/ are refused too
            if not path.startswith(os.path.realpath(HERE + "/view") + os.sep) or not os.path.isfile(path): return self.send(404, dict(error="not found"))
            self.send_file(path, mimetypes.guess_type(path)[0] or "application/octet-stream")
        elif u.path == "/api/preview.jpg":
            jpg = preview.get(q.get("source", "").strip()[:80])
            if jpg: self.send(200, jpg, "image/jpeg")
            else: self.send(204, b"")
        elif u.path == "/api/map":
            try: self.send(200, course(q.get("name", ""), q.get("track", "")))
            except ValueError as e: self.send(400, dict(error=str(e)))
        elif u.path == "/api/track":
            try: self.send(200, track(q.get("name", "")))
            except ValueError as e: self.send(400, dict(error=str(e)))
        elif u.path == "/api/topview.jpg":
            try: f = topview(q.get("map", ""), q.get("scene", ""), q.get("track", ""))
            except ValueError as e: return self.send(400, dict(error=str(e)))
            if f: self.send(200, open(f, "rb").read(), "image/jpeg")
            else: self.send(202, dict(rendering=True))
        else: self.send(404, dict(error="not found"))
    def do_POST(self):
        # JSON only: a page on another site can post a form here from the operator's browser, but not this content type
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json": return self.send(415, dict(error="application/json only"))
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n < 0 or n > 65536: return self.send(413, dict(error="bad size"))
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/start": start(body)
            elif self.path == "/api/stop": stop()
            elif self.path == "/api/grow": grow("手動")
            elif self.path == "/api/grow/reset": reset_map()
            elif self.path == "/api/view": set_view(body)
            elif self.path == "/api/cam": set_cam(body)
            elif self.path == "/api/out": set_out(body)
            elif self.path == "/api/rec/delete": recorders.delete(body.get("file"))
            else: return self.send(404, dict(error="not found"))
            self.send(200, dict(ok=True))
        except (ValueError, KeyError, TypeError) as e: self.send(400, dict(error=str(e)))
        except Exception as e: traceback.print_exc(); self.send(500, dict(error=f"{type(e).__name__}: {e}"))   # whatever it was, the page gets told
    def log_message(self, *a): pass

def on_term(*_):                                   # systemctl stop: the tracker gets TERM and the time to write its files (KillMode=mixed in the unit)
    threading.Thread(target=recorders.stop_all, daemon=True).start()   # the files are closed beside the tracker's write-out
    p = S["proc"]
    if p is not None:
        try:
            if S["phase"] != "stopping": p.send_signal(signal.SIGTERM)   # a second TERM would land in the tracker's write-out
            p.wait(40)
        except Exception: pass
    recorders.stop_all(); os._exit(0)
signal.signal(signal.SIGTERM, on_term)
threading.Thread(target=poll_gpu, daemon=True).start(); threading.Thread(target=poll_cpu, daemon=True).start(); threading.Thread(target=sample, daemon=True).start(); threading.Thread(target=between_heats, daemon=True).start()
print(f"control page on :{PORT}", flush=True)
http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
