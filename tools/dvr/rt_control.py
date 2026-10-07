"""rt_control.py: the field box's control page. Serves rt_control.html and starts / stops rt_track.py on an NDI source
(a 2x2 grid, one pilot per cell), so the box needs no keyboard at the venue: pick the source the Event VRX sends, name
the cells, start (the frame size and rate are read off the source). While the tracker runs it reads its output (signal on / off per cell), its PUSH stream
(frames processed and solved per cell, the positions for the page's top view) and the GPU's power from nvidia-smi. The page also
shows the source's picture: its low-bandwidth stream, two JPEGs a second, received only while a page asks for it.
usage (mastenv, from the folder with rt_track.py, the maps and the engines): python rt_control.py      # http://<box>:8080
env: PORT (8080), PUSH (8765: the tracker's answers, also what viewer/live.html reads), ENG (eng_linux),
     CONF (~/.ghostline-control.json: the last start, shown again when the page opens)
No login: whoever reaches the port can start and stop the tracker. Keep it on the venue LAN / Tailscale."""
import collections, glob, http.server, json, os, re, signal, subprocess, sys, threading, time, traceback, urllib.parse, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); E = os.environ.get
PORT, PUSH, ENG, CONF = int(E("PORT", 8080)), int(E("PUSH", 8765)), E("ENG", "eng_linux"), os.path.expanduser(E("CONF", "~/.ghostline-control.json"))
CELLS = ("tl", "tr", "bl", "br")                    # the grid's cells, row by row
# rt_track.py's knobs the page may set in "extra". Not any variable: the page has no login, and LD_PRELOAD is a variable too
KNOBS = set("NKF MIN_INL LOST RELOC_TOPK RELOC_MIN TOPK MAPK BATCH FRESH PIPE BA BA_PX BA_ACC BA_ALPHA RENDER RNEAR RFALL RCLIP ROPA RBATCH REDGE "
            "REACH NEAR FLOW FLOW_EVERY SIGNAL INFO PNP PL_DYN".split())
lock = threading.Lock(); gpu = {}
HIST = 300; hist = collections.deque(maxlen=HIST)   # one sample a second, for the page's sparklines: it shows the last 5 minutes whenever it is opened
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
        from cyndilib.receiver import Receiver
        from cyndilib.video_frame import VideoFrameSync
        from cyndilib.wrapper.ndi_recv import RecvColorFormat, RecvBandwidth
        while True:
            name = self.name
            src = next((s for s in finder.iter_sources() if name in s.name), None) if finder and name and self.wanted(name) else None
            if src is None: self.jpg = None; time.sleep(1); continue
            rx = Receiver(color_format=RecvColorFormat.BGRX_BGRA, bandwidth=RecvBandwidth.lowest)
            vf = VideoFrameSync(); rx.frame_sync.set_video_frame(vf); rx.set_source(src)
            try:
                while self.wanted(name):
                    rx.frame_sync.capture_video()
                    if vf.xres:
                        w, h = vf.get_resolution(); img = vf.get_array().reshape(h, w, 4)[..., :3]
                        img = cv2.resize(img, (640, round(h * 640 / w)), interpolation=cv2.INTER_AREA) if w > 640 else np.ascontiguousarray(img)
                        self.jpg, self.have = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])[1].tobytes(), name
                    time.sleep(0.5)
            except Exception as e: print("preview:", e, flush=True); time.sleep(2)
            finally: rx.disconnect(); self.jpg = None
preview = Preview()

courses = {}
def course(name):
    """the map's keyframes seen from above: their positions ([x east, z south] in metres, thinned to 700-1,400) show where the course
    runs; box is the ground the page draws (x0, z0, width, height), ytop the height the scan is looked down on from"""
    if name not in options()["maps"]: raise ValueError("地図が見つからない")
    if name not in courses:
        import numpy as np
        pos = np.load(f"{HERE}/{name}")["pos"]; y = pos[:, 1]; pad = 4
        # the smallest box around the flight paths: the middle 99% of the positions (a few stray ones widened it by 13 and 16 m) and 4 m around
        (x0, x1), (z0, z1) = np.percentile(pos[:, 0], [0.5, 99.5]), np.percentile(pos[:, 2], [0.5, 99.5])
        courses[name] = dict(pts=[[round(float(a), 2), round(float(b), 2)] for a, _, b in pos[::max(1, len(pos) // 700)]],
                             box=[round(float(v), 1) for v in (x0 - pad, z0 - pad, x1 - x0 + 2 * pad, z1 - z0 + 2 * pad)],
                             ytop=round(float(np.median(y)) + 30, 1))
    return courses[name]

rendering, failed = set(), {}                    # failed: file -> when; tried again after 2 minutes (a render during a heat may hit a full GPU)
def topview(name, scene):
    """the scan from straight above over the course's box (rt_topview.py, a few seconds, once per scene and map; the file is kept
    in live/). Returns the file, None while it is being rendered, and raises once a render has failed (not retried)."""
    c = course(name)
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
def options():
    if time.time() - _opt[0] < 2 and _opt[1]: return _opt[1]   # every open page asks once a second: the globs and the finder are read at most every 2 s
    rel = lambda ps: sorted(os.path.relpath(p, HERE) for p in ps)
    try: src = sorted(finder.get_source_names()) if finder else []
    except Exception: src = []
    # the lenses the pilots may fly; offered together, the tracker picks per cell (LENS_* in rt_track.py). A cell starts on the first:
    # the upgrade lens, which most pilots fly (7 of 8 in the FDF semifinal)
    lenses = sorted(rel(glob.glob(HERE + "/cams/*.json")), key=lambda f: ("upgrade" not in f, f))
    _opt[:] = [time.time(), dict(maps=rel(glob.glob(HERE + "/map_*.npz")), cams=(["+".join(lenses)] if len(lenses) > 1 else []) + lenses + rel(glob.glob(HERE + "/../*/dvr_pinhole.mp4.json")),
                                  scenes=sorted(glob.glob(os.path.expanduser("~/scenes/*.ply"))), sources=src)]
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
    on = [x for x in c.get("cells", []) if x.get("on")]
    if not on: raise ValueError("使うマスが 1 つも無い")
    names = [re.sub(r"[^A-Za-z0-9_-]", "", str(x.get("name") or ""))[:24] or str(x.get("cell")) for x in on]
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
        env.update(RENDER="1", RCLIP="2", SCENE=c["scene"])
    if c.get("ba"): env.update(BA="0.5", BA_ACC="30", BA_ALPHA="60")   # the outdoor setting, docs/realtime-tracking.ja.md
    for kv in str(c.get("extra", "")).split():
        k, _, v = kv.partition("=")
        if k not in KNOBS or not re.fullmatch(r"[A-Za-z0-9.]+", v): raise ValueError(f"追加の設定 {kv} は使えない")
        env[k] = v
    with lock:
        if S["phase"] != "stopped": raise ValueError("もう動いている")
        os.makedirs(HERE + "/live", exist_ok=True)
        p = subprocess.Popen([sys.executable, "-u", "rt_track.py", *specs], cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        S.update(phase="loading", proc=p, since=time.time(), exit=None, ndi=None, config=c, input=f"{w}×{h} · {fps:g} fps" + (f" → {cap:g}" if cap < fps else ""),
                 cells={n: dict(cell=x["cell"], signal=None, lens=None, rows=collections.deque(maxlen=600), last=None, trail=collections.deque(maxlen=150)) for x, n in zip(on, names)})
        S["log"].clear(); S["log"].append("$ rt_track.py " + " ".join(specs))
    json.dump({k: c.get(k) for k in ("source", "map", "cam", "scene", "render", "ba", "extra", "cells")}, open(CONF, "w"), ensure_ascii=False)
    threading.Thread(target=watch, args=(p,), daemon=True).start(); threading.Thread(target=answers, args=(p,), daemon=True).start()

def watch(p):                                       # the tracker's output: the log, and the lines that say how it is doing
    try:
        for line in p.stdout: note(line.rstrip()[:300])
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
            hist.append(dict(w=gpu.get("w"), temp=gpu.get("temp"), lat=latency(now) if on else None,
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
                    config=conf, cells=cells, log=list(S["log"])[-80:], gpu=dict(gpu), options=options(), push=PUSH,
                    hist=dict(n=HIST, w=[h["w"] for h in hist], temp=[h["temp"] for h in hist], lat=[h["lat"] for h in hist],
                              cells={c: [h["cells"].get(c) for h in hist] for c in CELLS}))

class Handler(http.server.BaseHTTPRequestHandler):
    timeout = 10                                    # a client that stops sending does not hold a thread for ever
    def send(self, code, body, ctype="application/json"):
        body = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application")) else "")); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        u = urllib.parse.urlsplit(self.path); q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/": self.send(200, open(HERE + "/rt_control.html", "rb").read(), "text/html")
        elif u.path == "/api/state": self.send(200, state())
        elif u.path == "/api/preview.jpg":
            jpg = preview.get(q.get("source", "").strip()[:80])
            if jpg: self.send(200, jpg, "image/jpeg")
            else: self.send(204, b"")
        elif u.path == "/api/map":
            try: self.send(200, course(q.get("name", "")))
            except ValueError as e: self.send(400, dict(error=str(e)))
        elif u.path == "/api/topview.jpg":
            try: f = topview(q.get("map", ""), q.get("scene", ""))
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
            else: return self.send(404, dict(error="not found"))
            self.send(200, dict(ok=True))
        except (ValueError, KeyError, TypeError) as e: self.send(400, dict(error=str(e)))
        except Exception as e: traceback.print_exc(); self.send(500, dict(error=f"{type(e).__name__}: {e}"))   # whatever it was, the page gets told
    def log_message(self, *a): pass

def on_term(*_):                                   # systemctl stop: the tracker gets TERM and the time to write its files (KillMode=mixed in the unit)
    p = S["proc"]
    if p is not None:
        try:
            if S["phase"] != "stopping": p.send_signal(signal.SIGTERM)   # a second TERM would land in the tracker's write-out
            p.wait(40)
        except Exception: pass
    os._exit(0)
signal.signal(signal.SIGTERM, on_term)
threading.Thread(target=poll_gpu, daemon=True).start(); threading.Thread(target=sample, daemon=True).start()
print(f"control page on :{PORT}", flush=True)
http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
