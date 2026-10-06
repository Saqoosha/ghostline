"""rt_control.py: the field box's control page. Serves rt_control.html and starts / stops rt_track.py on an NDI source
(a 2x2 grid, one pilot per cell), so the box needs no keyboard at the venue: pick the source the Event VRX sends, name
the cells, start (the frame size and rate are read off the source). While the tracker runs it reads its output (signal on / off per cell, cycle time), its PUSH stream
(frames processed and solved per cell) and the GPU's power from nvidia-smi.
usage (mastenv, from the folder with rt_track.py, the maps and the engines): python rt_control.py      # http://<box>:8080
env: PORT (8080), PUSH (8765: the tracker's answers, also what viewer/live.html reads), ENG (eng_linux),
     CONF (~/.ghostline-control.json: the last start, shown again when the page opens)
No login: whoever reaches the port can start and stop the tracker. Keep it on the venue LAN / Tailscale."""
import collections, glob, http.server, json, os, re, signal, subprocess, sys, threading, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); E = os.environ.get
PORT, PUSH, ENG, CONF = int(E("PORT", 8080)), int(E("PUSH", 8765)), E("ENG", "eng_linux"), os.path.expanduser(E("CONF", "~/.ghostline-control.json"))
CELLS = ("tl", "tr", "bl", "br")                    # the grid's cells, row by row
# rt_track.py's knobs the page may set in "extra". Not any variable: the page has no login, and LD_PRELOAD is a variable too
KNOBS = set("NKF MIN_INL LOST RELOC_TOPK RELOC_MIN TOPK MAPK BATCH FRESH PIPE BA BA_PX BA_ACC BA_ALPHA RENDER RNEAR RFALL RCLIP ROPA RBATCH REDGE "
            "REACH NEAR FLOW FLOW_EVERY SIGNAL INFO PNP PL_DYN".split())
lock = threading.Lock(); gpu = {}
HIST = 300; hist = collections.deque(maxlen=HIST)   # one sample a second, for the page's sparklines: it shows the last 5 minutes whenever it is opened
S = dict(phase="stopped", proc=None, since=None, exit=None, cycle=None, ndi=None, input=None, config=None, cells={}, log=collections.deque(maxlen=400))

try:                                                # the same finder rt_track.py uses; it keeps its list fresh on a thread
    from cyndilib.finder import Finder
    finder = Finder(); finder.open()
except Exception as e: finder = None; print("no NDI finder:", e, flush=True)

def options():
    rel = lambda ps: sorted(os.path.relpath(p, HERE) for p in ps)
    try: src = sorted(finder.get_source_names()) if finder else []
    except Exception: src = []
    return dict(maps=rel(glob.glob(HERE + "/map_*.npz")), cams=rel(glob.glob(HERE + "/../*/dvr_pinhole.mp4.json")),
                scenes=sorted(glob.glob(os.path.expanduser("~/scenes/*.ply"))), sources=src)

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
    if w % 2 or h % 2: raise ValueError(f"{full} は {w}x{h}：2×2 に割れない")
    specs = []
    for x, n in zip(on, names):
        if x.get("cell") not in CELLS: raise ValueError(f"{n}: マスの指定がおかしい")
        k = CELLS.index(x["cell"]); specs.append(f"{c['map']},ndi://{src}|{k % 2 * (w // 2)}:{k // 2 * (h // 2)}:{w // 2}:{h // 2}|{c['cam']},live/{n}")
    env = dict(os.environ, PATH=f"{os.path.dirname(sys.executable)}:/usr/local/cuda-12.9/bin:{os.environ.get('PATH', '')}",
               SIGNAL="1", GRID=f"{w}x{h}", GRID_FPS=f"{fps:g}", PUSH=str(PUSH))
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
        S.update(phase="loading", proc=p, since=time.time(), exit=None, cycle=None, ndi=None, config=c, input=f"{w}×{h} · {fps:g} fps",
                 cells={n: dict(cell=x["cell"], signal=None, rows=collections.deque(maxlen=600), last=None) for x, n in zip(on, names)})
        S["log"].clear(); S["log"].append("$ rt_track.py " + " ".join(specs))
    json.dump(c, open(CONF, "w"), ensure_ascii=False)
    threading.Thread(target=watch, args=(p,), daemon=True).start(); threading.Thread(target=answers, args=(p,), daemon=True).start()

def watch(p):                                       # the tracker's output: the log, and the lines that say how it is doing
    for line in p.stdout:
        line = line.rstrip()[:300]
        with lock:
            S["log"].append(line)
            if m := re.match(r"live/(.+): signal (on|off)$", line):
                if m[1] in S["cells"]: S["cells"][m[1]]["signal"] = m[2] == "on"
            elif m := re.match(r"(\d+) steps, ([\d.]+) ms each", line): S["cycle"] = float(m[2])
            elif m := re.search(r": receiving (.+)$", line): S["ndi"] = m[1]
            elif line == "listening" and S["phase"] == "loading": S["phase"] = "running"
    code = p.wait()
    with lock: S.update(phase="stopped", proc=None, exit=code); S["log"].append(f"(終了、コード {code})")

def answers(p):                                     # the PUSH stream: one row per processed frame, "pos" when it was solved
    while p.poll() is None:
        try:
            for raw in urllib.request.urlopen(f"http://127.0.0.1:{PUSH}/", timeout=86400):
                if not raw.startswith(b"data: "): continue
                r = json.loads(raw[6:]); now = time.time()
                with lock:
                    if c := S["cells"].get(r.get("stream")):
                        c["rows"].append((now, "pos" in r))
                        if "pos" in r: c["last"] = dict(t=now, inl=r.get("inl"), lat=r.get("lat"), how=r.get("how"))
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

def sample():
    while True:
        time.sleep(1); now = time.time()
        with lock:
            on = S["phase"] != "stopped"
            hist.append(dict(w=gpu.get("w"), temp=gpu.get("temp"), cycle=S["cycle"] if on else None,
                             cells={c["cell"]: round(sum(1 for t, ok in c["rows"] if ok and t > now - 1), 1) for c in S["cells"].values()} if on else {}))

def state():
    now = time.time(); hz = lambda rows, solved: round(sum(1 for t, ok in rows if t > now - 3 and (ok or not solved)) / 3, 1)
    with lock:
        cells = {n: dict(cell=c["cell"], signal=c["signal"], hz=hz(c["rows"], False), solved=hz(c["rows"], True),
                         last=c["last"] and dict(c["last"], age=round(now - c["last"]["t"], 1))) for n, c in S["cells"].items()}
        conf = S["config"]
        if conf is None and os.path.exists(CONF):
            try: conf = json.load(open(CONF))
            except Exception: conf = None
        return dict(phase=S["phase"], up=S["since"] and S["phase"] != "stopped" and round(now - S["since"]), exit=S["exit"], cycle=S["cycle"], ndi=S["ndi"], input=S["input"],
                    config=conf, cells=cells, log=list(S["log"])[-80:], gpu=dict(gpu), options=options(), push=PUSH,
                    hist=dict(n=HIST, w=[h["w"] for h in hist], temp=[h["temp"] for h in hist], cycle=[h["cycle"] for h in hist],
                              cells={c: [h["cells"].get(c) for h in hist] for c in CELLS}))

class Handler(http.server.BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        body = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", ctype + "; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/": self.send(200, open(HERE + "/rt_control.html", "rb").read(), "text/html")
        elif self.path == "/api/state": self.send(200, state())
        else: self.send(404, dict(error="not found"))
    def do_POST(self):
        # JSON only: a page on another site can post a form here from the operator's browser, but not this content type
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json": return self.send(415, dict(error="application/json only"))
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/start": start(body)
            elif self.path == "/api/stop": stop()
            else: return self.send(404, dict(error="not found"))
            self.send(200, dict(ok=True))
        except (ValueError, KeyError, TypeError) as e: self.send(400, dict(error=str(e)))
    def log_message(self, *a): pass

threading.Thread(target=poll_gpu, daemon=True).start(); threading.Thread(target=sample, daemon=True).start()
print(f"control page on :{PORT}", flush=True)
http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
