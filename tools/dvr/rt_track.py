"""Live tracker, replayed: place each DVR frame on the scan using only the frames before it, against the keyframe map
from rt_map.py, and time every step. Per frame: predict the pose from the last solved ones (constant velocity), pick the
keyframes nearest to the prediction, XFeat on the frame + LighterGlue against their stored keypoints, PnP-RANSAC on the
2D-3D matches. No answer for LOST seconds (or at the start): DINOv2 retrieval over all keyframes.
Several pilots at once: every cycle takes the newest arrived frame of each stream, runs XFeat on all of them in one
batch and every (frame, keyframe) pair - relocalization candidates included - through LighterGlue in one batch, and the
PnPs in threads. Batching is the point: 8 pairs in one LighterGlue call take ~19 ms, one pair alone ~14 ms (4090, PyTorch).
LIVE=1 replays at the videos' rate: frame i arrives at i/fps, a cycle takes the newest frame of each stream that has
arrived and the ones in between are dropped, as on a capture card. Scored against the offline poses (truth, e.g.
poses60_pad.json; src ba / ba-fill / takeoff / ground). The page draws its own dot from the answers (viewer/src/live.ts,
100 ms behind); the .json / _trail.json here are the extrapolated dot and trail, kept for scoring the replay.
Live input (GRID=WxH): a 2x2 broadcast grid (the Event VRX over NDI -> OBS -> SRT) instead of files. The video field is
"<url>|x:y:w:h|<cam.json>": one ffmpeg per url decodes it on a thread, and each frame's cell (the pilot's 4:3 fisheye
stretched to the cell) is remapped straight to the map's pinhole size. Then the clock is the real one, each cycle takes
the newest frame of every cell, and each answer records its latency from the frame's arrival (and, with SEND_T0 = the
sender's wall-clock start and FRAME_CODE, from the moment it was sent - the two machines' clocks must agree).
usage (mastenv + kornia + poselib, ~/xfeat; tensorrt for GLUE / XFEAT): rt_track.py map.npz,dvr_pinhole.mp4,out_prefix[,truth.json] [more streams ...]
  GRID=1280x720 GRID_FPS=30 rt_track.py "map.npz,srt://0.0.0.0:9000?mode=listener|0:0:640:360|cam.json,out_prefix" ...
  writes per stream out_prefix.jsonl (per processed frame: pose, how it was found; the viewer's live replay loads its
  solved rows as a JSON array), out_prefix.json (the extrapolated dot, poses60 format) and out_prefix_trail.json
env: LIVE (1), NKF (2) keyframes matched per frame, MIN_INL (30), LOST (0.33 s without an answer before relocalizing),
     RELOC_TOPK (5), RELOC_MIN (50), TOPK (2048) keypoints on the frame, MAPK (0 = all) per keyframe, NMAX (0 = all frames), BATCH (16 pairs per call),
     FRESH (0.1 s: an answer this old or newer is labelled "rt", older "rt-carry"), KQ (1e4, the drawn position's jerk density (m/s^3)^2 s),
     BLEND (0.025 s; 0 = draw the filter as it jumps), TRAIL_L (0.2 s the trail is drawn late), TRAIL_W (0.25 s either
     side in its fit), PIPE (2, 1 with RENDER: overlap the next cycle's GPU work with this cycle's PnP, below),
     FLOW (0; 1 = carry points to the next frame with LK instead of matching, below), FLOW_EVERY (8), FLOW_RESEED (60), FLOW_MIN (30),
     PUSH (0; a port: answers out as server-sent events while it runs, for viewer/live.html),
     INFO (0; 1 = rows carry H), BA (0; seconds of answers solved again together after every answer, below),
     GLUE / XFEAT (unset; TensorRT engines from rt_trt.py to match / find features with instead of PyTorch),
     PNP (poselib; magsac = OpenCV's USAC_MAGSAC, below),
     RENDER (0; 1 = while tracking, match a render of SCENE (.ply) at the prediction instead of keyframes, rt_render.py), RNEAR (1.0 m near plane),
     RFALL (1: also match keyframes right after a failed try); RCLIP / ROPA / RBATCH / REDGE: see rt_render.py"""
import os
# One BLAS thread. numpy's matrices here are small (the BA's system is about 150 x 150), and OpenBLAS otherwise starts a
# thread per core for them: on a 32-core machine that took the tracker from 54 to 33 Hz with BA on, the solve itself 0.2 ms.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
# One OpenMP thread for torch's CPU ops too: its 16 workers spin between the small ops here and held the CPU package at
# ~100 W whatever the load (4090 box, d05 alone 104 -> 56 W, four pilots 94 -> 62 W; Hz, latency and accuracy the same)
os.environ.setdefault("OMP_NUM_THREADS", "1")
import json, sys, time, math, numpy as np, torch, cv2
# CUDA_SYNC=block: a thread waiting on the GPU sleeps instead of spinning a core. Set on the primary context before torch
# makes it (TensorRT shares it). Native Windows honours it (a .item() loop: 1.02 s of CPU in 1.04 s -> 0.00 s); WSL ignores it.
if os.environ.get("CUDA_SYNC"):
    import ctypes; _cu = ctypes.WinDLL("nvcuda.dll") if os.name == "nt" else ctypes.CDLL("libcuda.so.1"); _d = ctypes.c_int()
    assert _cu.cuInit(0) == 0 and _cu.cuDeviceGet(ctypes.byref(_d), 0) == 0
    assert _cu.cuDevicePrimaryCtxSetFlags(_d, {"spin": 1, "yield": 2, "block": 4}[os.environ["CUDA_SYNC"]]) == 0
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.expanduser(os.environ.get("XFEAT_DIR", "~/xfeat"))); from modules.xfeat import XFeat
from scipy.spatial.transform import Rotation as Rot, Slerp
dev = os.environ.get("DEV", "cuda"); E = lambda k, d: float(os.environ.get(k, d))   # DEV=mps runs on Apple silicon
LIVE, NKF, MIN_INL, LOST = int(E("LIVE", 1)), int(E("NKF", 2)), int(E("MIN_INL", 30)), E("LOST", 0.33)
RTOPK, RMIN, TOPK, NMAX, BATCH = int(E("RELOC_TOPK", 5)), int(E("RELOC_MIN", 50)), int(E("TOPK", 2048)), int(E("NMAX", 0)), int(E("BATCH", 16))
PNP = os.environ.get("PNP", "poselib")           # poselib (PoseLib's LO-RANSAC) or magsac (OpenCV USAC_MAGSAC)
if PNP == "poselib": import poselib
RENDER, SCENE, RNEAR, RFALL = int(E("RENDER", 0)), os.environ.get("SCENE"), E("RNEAR", 1.0), int(E("RFALL", 1))
PL_DYN = E("PL_DYN", 1.0)                           # PoseLib: trials x this over what success_prob 0.99 needs
MAPK = int(E("MAPK", 0))                            # keypoints per keyframe used from the map (0 = all)
# NEAR: after a failed attempt, match NEAR_K keyframes around the prediction up to NEAR_ANG degrees off, and add NEAR_K
# around the last position to the global retrieval's candidates - a fast turn breaks the narrow search before anything else.
# M1 Max, one pilot: relocalizations 79 -> 31, answers 13.7 -> 16.4 Hz, drawn 1.96 -> 1.61 m p50. Four pilots on the 4090:
# worse (a failing pilot's extra pairs slow the shared batch for everyone), so off by default
# REACH (m/s): the global retrieval only looks at keyframes within 5 m + REACH x the time since the last answer of where
# it was last seen. 0 = everywhere (as at the start)
REACH = E("REACH", 0)
NEAR, NEAR_K, NEAR_ANG = int(E("NEAR", 0)), int(E("NEAR_K", 4)), E("NEAR_ANG", 100)
FLOW, FLOW_EVERY, FLOW_RESEED, FLOW_MIN = int(E("FLOW", 0)), int(E("FLOW_EVERY", 8)), int(E("FLOW_RESEED", 60)), int(E("FLOW_MIN", 30))
GRID, GRID_FPS, SEND_T0, FRAME_CODE = os.environ.get("GRID"), E("GRID_FPS", 30), E("SEND_T0", 0), os.environ.get("FRAME_CODE")
# PUSH (port): every answer goes out the moment it is solved, as server-sent events (one JSON object per event: the
# .jsonl row plus "stream" and "fps"), for the live page (viewer/live.html). 0 = off.
INFO = int(E("INFO", 0))                           # 1: every answer row carries "H", its 6x6 information (upper triangle, 21 numbers)
# BA (s): after every answer, the answers of the last BA seconds are solved again together - each one a 6-dof measurement
# with its own information, tied by priors on linear and angular acceleration - and the row carries the revised poses
# ("win": [frame, x, y, z, qx, qy, qz, qw] per answer in the window). The answer itself (pos, quat) stays the raw one.
# Against the page's quadratic through raw positions (the Whoop clip, drawn 0.1 s late): wobble 3.0 -> 2.2 cm, error
# p90 24 -> 21.5 cm, p99 80 -> 51 cm, rotation p90 3.55 -> 2.83 deg; at 0.2 s and more the two draw the same line.
# BA_PX is the pixel noise the information is read at, BA_ACC m/s^2 and BA_ALPHA rad/s^2 the priors (a Tiny Whoop
# indoors; while the angular term stays under its Huber threshold only their ratio to BA_PX matters).
BA, BA_PX, BA_ACC, BA_ALPHA = E("BA", 0), E("BA_PX", 8), E("BA_ACC", 3), E("BA_ALPHA", 10)
PUSH = int(E("PUSH", 0)); subs = []
if PUSH:
    import http.server, queue, threading
    class _Push(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            for k, v in (("Content-Type", "text/event-stream"), ("Cache-Control", "no-cache"), ("Access-Control-Allow-Origin", "*")): self.send_header(k, v)
            self.end_headers(); q = queue.Queue(2000); subs.append(q)   # a reader that stalls loses rows instead of growing the queue
            try:
                while True: self.wfile.write(f"data: {q.get()}\n\n".encode()); self.wfile.flush()
            except OSError: pass
            finally: subs.remove(q)
        def log_message(self, *a): pass
    threading.Thread(target=http.server.ThreadingHTTPServer(("0.0.0.0", PUSH), _Push).serve_forever, daemon=True).start()
FRESH, KQ, BLEND, TRAIL_L, TRAIL_W = E("FRESH", 0.1), E("KQ", 1e4), E("BLEND", 0.025), E("TRAIL_L", 0.2), E("TRAIL_W", 0.25)
T = lambda a: torch.tensor(a, dtype=torch.float32, device=dev)
def sync(): torch.cuda.synchronize() if dev == "cuda" else torch.mps.synchronize() if dev == "mps" else None
maps, sources = {}, {}
import threading, subprocess
# SIGNAL=1 (live): a cell whose receiver shows no picture - its flat no-signal screen or analog snow - sends no frames to
# the tracker, and with every cell quiet the loop only waits. On an EventVRX recording (2x2, analog, 707 s) the flat screens
# (grey, blue, black) have a pixel std of 0 and pictures 8 and up; snow is told apart by the correlation of neighbouring
# rows, 0.1-0.3 against 0.8-0.9 for a picture (0.5-0.7: a weak signal with a faint picture). The cells had a picture 47% of
# the time, all four were quiet 13% of it, and ~0.1 s of flight was dropped. HDZero (FDF semifinal 2x2): flat grey or black,
# breakup 0.1-0.5; live over SRT, four cells, RENDER=1: 251 -> 129 W on average (60 W between heats), solved frames -3%.
SIGNAL = int(E("SIGNAL", 0))
def has_signal(cell):                               # BGR crop of one cell
    g = cv2.cvtColor(cv2.resize(cell, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)[4:86, 4:156].astype(np.float32)
    if g.std() < 2: return False
    a0, a1 = g[:-1] - g[:-1].mean(), g[1:] - g[1:].mean()
    return float((a0 * a1).sum() / (np.sqrt((a0 ** 2).sum() * (a1 ** 2).sum()) + 1e-6)) >= 0.5
class GridSource:                                   # one decoded live video; its frames fan out to the cells that use it
    def __init__(self, url):
        self.url, self.cells, self.ended, self.n = url, [], False, 0
        self.W, self.H = [int(v) for v in GRID.split("x")]
    def start(self):
        if self.url.startswith("ndi://"):           # an NDI source on the LAN (the EventVRX output at the venue), by (part of) its name
            threading.Thread(target=self.run_ndi, daemon=True).start(); return
        # GRID_GST: receive RTP over SRT with GStreamer instead (rt_send.py GST=1). Mac-local, send -> decoded: ffmpeg + mpegts
        # ~200 ms, GStreamer + mpegts 164 ms, GStreamer + RTP 125 ms (all with SRT latency 80 ms); mpegts itself holds ~35 ms.
        # The url's port and latency are reused; the url field still names the source the cells share.
        if os.environ.get("GRID_GST"):
            port = self.url.split("//")[1].split("?")[0].split(":")[-1]; lat = int(self.url.split("latency=")[1].split("&")[0]) // 1000 if "latency=" in self.url else 80
            # GRID_CROP (left:top:right:bottom px): cut the decoded frame before it is scaled to GRID and piped here. One pilot on
            # a 1280x720 capture with the 4:3 picture in the middle: GRID_CROP=160:0:160:0 GRID=640x480, cell 0:0:640:480 -
            # the pipe then carries 0.9 MB a frame instead of 2.8 (at 60 fps Python was reading 166 MB/s to use a third of it).
            c = os.environ.get("GRID_CROP"); crop = "videocrop left={} top={} right={} bottom={} ! ".format(*c.split(":")) if c else ""
            cmd = (f"gst-launch-1.0 -q srtsrc uri=srt://:{port}?mode=listener latency={lat} ! application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000"
                   f" ! rtph264depay ! h264parse ! avdec_h264 ! {crop}videoconvert ! videoscale ! video/x-raw,format=BGR,width={self.W},height={self.H} ! fdsink fd=1 sync=false")
            self.proc = subprocess.Popen(cmd.split(), stdout=subprocess.PIPE, bufsize=0)
            threading.Thread(target=self.run, daemon=True).start(); return
        # small probe: ffmpeg's default reads ~5 s of the stream before the first frame comes out
        self.proc = subprocess.Popen(["ffmpeg", "-v", "error", "-fflags", "nobuffer", "-flags", "low_delay", "-probesize", "500000", "-analyzeduration", "200000",
                                      "-i", self.url, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{self.W}x{self.H}", "-"], stdout=subprocess.PIPE, bufsize=0)
        threading.Thread(target=self.run, daemon=True).start()
    def run(self):                                  # a failure here must end the run, not leave the loop waiting forever
        try: self.read()
        finally:
            self.ended = True
            try: rc = self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired: rc = None
            if rc: print(f"{self.url}: decoder exited with {rc}", flush=True)
    def run_ndi(self):
        try: self.read_ndi()
        finally: self.ended = True
    def read_ndi(self):
        # cyndilib (pip; it bundles libndi) with the FrameSync API, polled every 2 ms: it hands over the newest frame, and a frame
        # is new when its NDI timestamp changes. Polling VideoRecvFrame with receive() lost a third of the frames although the
        # library had them all. 1920x1080 30 fps on the 4090 box (sender on the same box): 30.0 fps, none dropped, sent -> here
        # 31.5 ms p50, 0.6 ms to copy a frame out. Linux needs avahi-daemon running or the source is never found.
        from cyndilib.finder import Finder
        from cyndilib.receiver import Receiver
        from cyndilib.video_frame import VideoFrameSync
        from cyndilib.wrapper.ndi_recv import RecvColorFormat, RecvBandwidth
        name = self.url[len("ndi://"):]; finder = Finder(); finder.open(); src = None
        while src is None:
            finder.wait(1); src = next((s for s in finder.iter_sources() if name in s.name), None)
        print(f"{self.url}: receiving {src.name}", flush=True)
        rx = Receiver(color_format=RecvColorFormat.BGRX_BGRA, bandwidth=RecvBandwidth.highest)
        vf = VideoFrameSync(); rx.frame_sync.set_video_frame(vf); rx.set_source(src); last = None
        while True:
            rx.frame_sync.capture_video()
            ts = vf.get_timestamp_posix() if vf.xres else None
            if ts and ts != last:
                last = ts; t, tw = time.perf_counter(), time.time(); w, h = vf.get_resolution()
                full = cv2.cvtColor(vf.get_array().reshape(h, w, 4), cv2.COLOR_BGRA2BGR)
                if (w, h) != (self.W, self.H): full = cv2.resize(full, (self.W, self.H), interpolation=cv2.INTER_AREA)
                for s in self.cells: s.push(self.n, full, t, tw)
                self.n += 1
            time.sleep(0.002)
    def read(self):
        size = self.W * self.H * 3
        while True:
            buf = bytearray()
            while len(buf) < size:
                chunk = self.proc.stdout.read(size - len(buf))
                if not chunk: break
                buf += chunk
            if len(buf) < size: break
            t, tw = time.perf_counter(), time.time(); full = np.frombuffer(bytes(buf), np.uint8).reshape(self.H, self.W, 3)
            n = self.n
            if FRAME_CODE:                          # the sender's frame number, 16 black/white blocks (x:y of the first, 36 px apart)
                x, y = [int(v) for v in FRAME_CODE.split(":")]
                v = [full[y + 5:y + 35, x + 36 * b + 4:x + 36 * b + 28].mean() for b in range(16)]
                # a broken frame decodes grey (the empty cell is ~130): every block must read clearly black or white
                if any(60 <= a <= 190 for a in v): continue
                n = sum(1 << b for b in range(16) if v[b] > 190)
                if self.cells and n < self.cells[0].N: continue   # never step back
            for s in self.cells: s.push(n, full, t, tw)
            self.n += 1
class Stream:
    def __init__(self, spec):
        f = spec.split(","); self.map_npz, self.video, self.prefix = f[:3]; self.truth = f[3] if len(f) > 3 else None
        self.live = "|" in self.video
        if self.live: url, rect, camj = self.video.split("|"); self.rect = [int(v) for v in rect.split(":")]; self.video = camj[:-5] if camj.endswith(".json") else camj
        if self.map_npz not in maps:
            m = np.load(self.map_npz); mk = MAPK or m["kp"].shape[1]   # XFeat returns the strongest first, so the first MAPK are the best
            maps[self.map_npz] = dict(K=m["K"], size=[int(x) for x in m["size"]], n=np.minimum(m["n"], mk), kp=T(m["kp"][:, :mk]), desc=torch.tensor(m["desc"][:, :mk], device=dev), X=m["X"][:, :mk],
                                      pos=m["pos"], fwd=Rot.from_quat(m["quat"]).as_matrix()[:, :, 2], g=T(m["g"]))
        self.m = maps[self.map_npz]; self.K = self.m["K"]; RW, RH = self.m["size"]
        # frames: the pinhole video, at the map's size, with the OSD and the undistortion border masked (as cpr_track.py)
        cam = self.cam = json.load(open(self.video + ".json")); W, H = cam["width"], cam["height"]; self.fps = GRID_FPS if self.live else cam.get("fps", 60)
        Kfull = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
        fk = cam["source_fisheye"]; Kf = np.array([[fk["fx"], 0, fk["cx"]], [0, fk["fy"], fk["cy"]], [0, 0, 1]])
        m1, m2 = cv2.fisheye.initUndistortRectifyMap(Kf, np.array(fk["k"]), np.eye(3), Kfull, (W, H), cv2.CV_16SC2)
        osd = np.full((H, W), 255, np.uint8); osd[:82] = 0; osd[632:] = 0
        self.qvalid = T(cv2.resize((cv2.erode(cv2.remap(osd, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0), np.ones((9, 9), np.uint8)) > 0).astype(np.uint8), (RW, RH), interpolation=cv2.INTER_NEAREST)) > 0
        self.next = 0; self.hist = []; self.recs = []; self.flow = None; self.fails = 0; self.last = None
        if self.live:                               # cell pixel for every pinhole pixel of the map's size: 4:3 fisheye stretched to the cell
            x0, y0, w, h = self.rect; Km = self.K
            mx, my = cv2.fisheye.initUndistortRectifyMap(Kf, np.array(fk["k"]), np.eye(3), Km, (RW, RH), cv2.CV_32FC1)
            self.mx, self.my = mx * w / W + x0, my * h / H + y0
            self.frames, self.arrive, self.arrive_wall = {0: np.zeros((RH, RW, 3), np.uint8)}, {}, {}; self.N = 1; self.signal, self.flip = None, 0   # a blank frame for the warm-up
            src = sources.setdefault(url, GridSource(url)); src.cells.append(self); self.src = src
            print(f"{self.prefix}: live cell {rect} of {url}, {len(self.m['pos'])} keyframes", flush=True); return
        cap = cv2.VideoCapture(self.video); self.frames = []
        while True:
            ok, fr = cap.read()
            if not ok or (NMAX and len(self.frames) >= NMAX): break
            self.frames.append(cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), (RW, RH), interpolation=cv2.INTER_AREA))
        self.N = len(self.frames)
        print(f"{self.prefix}: {self.N} frames at {self.fps} fps, {len(self.m['pos'])} keyframes", flush=True)
    def push(self, n, full, t, tw):                  # reader thread: a new frame of the source
        if SIGNAL:                                  # no picture in the cell: no frame, so a quiet cell costs nothing
            on = has_signal(full[self.rect[1]:self.rect[1] + self.rect[3], self.rect[0]:self.rect[0] + self.rect[2]])
            self.flip = self.flip + 1 if on != self.signal else 0   # logged once it has held 0.5 s; a weak signal flickers
            if self.flip >= self.fps / 2: self.signal, self.flip = on, 0; print(f"{self.prefix}: signal {'on' if on else 'off'}", flush=True)
            if not on: return
        self.frames[n] = cv2.cvtColor(cv2.remap(full, self.mx, self.my, cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
        if not self.arrive: self.t_base = t - n / self.fps   # when frame 0 would have arrived, even if it was lost
        self.arrive[n], self.arrive_wall[n] = t, tw; self.N = n + 1
        for k in [k for k in self.frames if k < n - 30]: del self.frames[k]   # codes skip on drops; 30 outlasts a slow cycle
    def nearest(self, R, p, n=None, max_ang=45, per_deg=15):   # keyframes near the predicted pose, looking about the same way
        d = np.linalg.norm(self.m["pos"] - p, axis=1); ang = np.degrees(np.arccos(np.clip(self.m["fwd"] @ R.as_matrix()[:, 2], -1, 1)))
        score = d / 1.5 + ang / per_deg; score[(d > 12) | (ang > max_ang)] = np.inf
        return [int(k) for k in np.argsort(score)[:n or NKF] if np.isfinite(score[k])]
streams = [Stream(s) for s in sys.argv[1:]]; RW, RH = streams[0].m["size"]
assert all(s.m["size"] == [RW, RH] for s in streams), "all maps must share the render size"
# ---- models
xfeat = XFeat(top_k=TOPK)
from modules.lighterglue import LighterGlue; LighterGlue.default_conf_xfeat["mp"] = False   # autocast fp16: d05 solved 1,630 -> 761 frames, not faster
LighterGlue.default_conf_xfeat["width_confidence"] = -1   # point pruning works only one pair at a time; the batch is worth more
lg = LighterGlue().eval()
glue = xf = None                                    # LighterGlue / XFeat as TensorRT engines (rt_trt.py)
if os.environ.get("GLUE"):                          # both sides must be TOPK points
    assert dev == "cuda" and streams[0].m["kp"].shape[1] == TOPK, "GLUE needs cuda and TOPK points per map keyframe"
    from rt_trt import Trt; glue = Trt(os.environ["GLUE"])
def detect(img):                                    # [B, 3, H, W] in 0..1 -> keypoints [B, TOPK, 2], descriptors [B, TOPK, 64], score (-1 = none)
    if xf: return xf(img)
    with torch.no_grad(): fs = xfeat.detectAndCompute(img, top_k=TOPK)
    kp, d, sc = torch.zeros(len(fs), TOPK, 2, device=dev), torch.zeros(len(fs), TOPK, 64, device=dev), torch.full((len(fs), TOPK), -1.0, device=dev)
    for b, f in enumerate(fs): n = len(f["keypoints"]); kp[b, :n], d[b, :n], sc[b, :n] = f["keypoints"], f["descriptors"], f["scores"]
    return kp, d, sc
if os.environ.get("XFEAT"):
    assert dev == "cuda", "XFEAT needs cuda"
    from rt_trt import Trt; xf = Trt(os.environ["XFEAT"])
if dev != "cuda":                                   # both pick cuda-or-cpu themselves
    xfeat.dev = lg.dev = torch.device(dev); xfeat.net.to(dev); lg.net.to(dev)
dino = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14").to(dev).eval()
scene = None
if RENDER:
    assert dev == "cuda" and SCENE, "RENDER needs cuda and SCENE=<the map's .ply>"
    from rt_render import Scene; scene = Scene(SCENE, RW, RH, RNEAR, TOPK, detect)
    scene.views([(Rot.identity(), np.zeros(3))] * len(streams), [s.K for s in streams])   # the first render compiles kernels: not on a tracked frame
MEAN, STD = T([0.485, 0.456, 0.406])[None, :, None, None], T([0.229, 0.224, 0.225])[None, :, None, None]
SIZE = T([[RW, RH]])
def features(batch):                                # [(stream, frame)] -> per frame (keypoints, descriptors[, mask]), and the tensor
    x = torch.stack([torch.from_numpy(s.frames[i]) for s, i in batch]).to(dev).permute(0, 3, 1, 2).float() / 255
    if xf:                                          # the engine's fixed TOPK points; mask = a real point (score > 0) off the OSD / border: no per-frame cut, no wait
        kp, d, sc = xf(x); ix = kp.long(); qv = torch.stack([s.qvalid for s, _ in batch])
        ok = (sc > 0) & qv[torch.arange(len(batch), device=dev)[:, None], ix[..., 1], ix[..., 0]]
        return [(kp[b], d[b], ok[b]) for b in range(len(batch))], x
    with torch.no_grad(): fs = xfeat.detectAndCompute(x, top_k=TOPK)
    out = []
    for (s, _), f in zip(batch, fs):                # keypoints off the OSD / border
        uv = f["keypoints"]; ix = uv.round().long(); ok = s.qvalid[ix[:, 1].clamp(0, RH - 1), ix[:, 0].clamp(0, RW - 1)]
        out.append((uv[ok], f["descriptors"][ok]))
    return out, x
def ref(s, k):                                      # a keyframe narrower than TOPK padded (as a render view) so the two stack in one batch
    if not isinstance(k, int): return k["kp"], k["desc"], k["X"], k["n"]
    p = TOPK - s.m["kp"].shape[1]; pad = lambda t: torch.nn.functional.pad(t, (0, 0, 0, p)) if p > 0 else t
    return pad(s.m["kp"][k]), pad(s.m["desc"][k]), s.m["X"][k], s.m["n"][k]
def match_all(pairs):                               # [(query, stream, keyframe index or rt_render view)] -> per pair (uv on the frame, X in the world)
    res = []
    for c in range(0, len(pairs), BATCH):
        chunk = pairs[c:c + BATCH]; B = len(chunk)
        if len(chunk[0][0]) == 3:                   # fixed TOPK with a mask: masked points zeroed (the matcher does not care about order), their matches dropped
            ok0 = torch.stack([q[2] for q, _, _ in chunk]); kp0 = torch.stack([q[0] for q, _, _ in chunk]) * ok0[..., None]
            d0 = torch.stack([q[1] for q, _, _ in chunk]) * ok0[..., None]
        else:                                       # pad the query to TOPK points; matches onto padding are dropped
            kp0 = torch.zeros(B, TOPK, 2, device=dev); d0 = torch.zeros(B, TOPK, 64, device=dev); n0 = []
            for b, ((uv, d), s, k) in enumerate(chunk): kp0[b, :len(uv)] = uv; d0[b, :len(uv)] = d; n0.append(len(uv))
            ok0 = torch.arange(TOPK, device=dev)[None] < torch.tensor(n0, device=dev)[:, None]
        mp = chunk[0][1].m; rf = [ref(s, k) for _, s, k in chunk]
        if all(isinstance(k, int) and s.m is mp for _, s, k in chunk):
            ks = torch.tensor([k for _, _, k in chunk], device=dev); kp1, d1 = mp["kp"][ks], mp["desc"][ks].float()
        else: kp1, d1 = torch.stack([r[0] for r in rf]), torch.stack([r[1].float() for r in rf])
        if glue: m0 = glue(kp0, d0, kp1, d1)[0]
        else:
            with torch.no_grad():
                m0 = lg.net({"image0": {"keypoints": kp0, "descriptors": d0, "image_size": SIZE.expand(B, 2)},
                             "image1": {"keypoints": kp1, "descriptors": d1, "image_size": SIZE.expand(B, 2)}})["matches0"]
        m0, ok0, kp0 = m0.cpu().numpy(), ok0.cpu().numpy(), kp0.cpu().numpy()   # one copy each for the chunk, not one per pair
        for b, r in enumerate(rf):
            i = np.nonzero((m0[b] >= 0) & ok0[b])[0]; j = m0[b][i]; keep = j < r[3]
            res.append((kp0[b][i[keep]], r[2][j[keep]]))
    return res
def pnp(K, uv, X):
    # PoseLib, not OpenCV: on the tracker's own matches (d05, ~900 from two keyframes, 33% inliers) MAGSAC takes 11 ms
    # (its scoring, not its 500 iterations: 200 is as slow) and PoseLib 2.8 ms (PL_DYN 1), and the answers come out closer to the
    # offline path (p50 0.24 -> 0.19 m). MAGSAC in turn beat SQPnP in a plain RANSAC (12 ms, none kept at 30% inliers, synthetic).
    # Narrowing the matches around the predicted pose first did not help.
    if len(uv) < 12: return None
    if PNP == "poselib":                            # P3P + LO-RANSAC in C++; it lets go of the GIL, so the PnP threads run side by side
        # dyn_num_trials_mult: PoseLib runs 3x the trials its success_prob asks for by default (~377 at 33% inliers instead of ~126)
        pose, r = poselib.estimate_absolute_pose(uv.astype(np.float64), X.astype(np.float64), {"model": "PINHOLE", "width": RW, "height": RH, "params": [K[0, 0], K[1, 1], K[0, 2], K[1, 2]]},
                                                 {"max_reproj_error": 3.0, "max_iterations": 500, "min_iterations": 10, "success_prob": 0.99, "dyn_num_trials_mult": PL_DYN}, {})
        inl = np.flatnonzero(r["inliers"])[:, None]
        if len(inl) < 12: return None
        rv, tv = cv2.Rodrigues(pose.R)[0], pose.t.reshape(3, 1).copy()
    else:
        ok, rv, tv, inl = cv2.solvePnPRansac(X.astype(np.float64), uv.astype(np.float64), K, None, iterationsCount=500, reprojectionError=3.0, flags=cv2.USAC_MAGSAC)
        if not ok or inl is None or len(inl) < 12: return None
    rv, tv = cv2.solvePnPRefineLM(X[inl[:, 0]].astype(np.float64), uv[inl[:, 0]].astype(np.float64), K, None, rv, tv)
    Rw = cv2.Rodrigues(rv)[0]; c = -Rw.T @ tv[:, 0]
    return Rot.from_matrix(Rw.T), c, len(inl), inl[:, 0], (info(K, Rw, c, X[inl[:, 0]]) if INFO or BA else None)
def info(K, Rw, c, X):
    # What this answer's own points say about its pose: the 6x6 information J^T J of the reprojection at the solution, for
    # 1 px of noise, over (rotation about the camera's own axes, position in the world) - cpr_ba.py's frame_terms. A
    # smoother over several answers needs it: an answer is tight across some directions and loose along others, and its
    # rotation and position errors go together.
    d = (X - c) @ Rw.T; z = d[:, 2]; fx, fy = K[0, 0], K[1, 1]
    Ju = np.zeros((len(d), 2, 3)); Ju[:, 0, 0] = fx / z; Ju[:, 0, 2] = -fx * d[:, 0] / z**2; Ju[:, 1, 1] = fy / z; Ju[:, 1, 2] = -fy * d[:, 1] / z**2
    dx = np.zeros((len(d), 3, 3)); dx[:, 0, 1], dx[:, 0, 2], dx[:, 1, 0], dx[:, 1, 2], dx[:, 2, 0], dx[:, 2, 1] = -d[:, 2], d[:, 1], d[:, 2], -d[:, 0], -d[:, 1], d[:, 0]
    J = np.concatenate([Ju @ dx, Ju @ (-Rw)[None]], axis=2)
    return np.einsum("mki,mkj->ij", J, J)
def smooth(win):
    # win: [(t, frame, Rot, pos, H, R matrix)] in time order -> revised (quat, pos) per entry. Unknown per answer: a small turn about
    # its own camera axes and its position; every answer's reprojection is the quadratic its H gives around its own
    # solution, so the system is linear. Answers more than 0.3 s apart are not tied (tracking was lost between them).
    # Built with whole-array operations: looped per answer it took the main thread ~10 ms on a 25-answer window and
    # the tracker fell from 54 to 34 Hz on a 60 fps flight.
    n = len(win); N = 6 * n; t = np.array([w[0] for w in win]); Rm = np.stack([w[5] for w in win]); Pm = np.stack([w[3] for w in win])
    Hs = np.stack([w[4] for w in win]) / BA_PX ** 2
    M = np.zeros((N, N)); Mv = M.reshape(n, 6, n, 6); k = np.arange(n); Mv[k, :, k, :] = Hs
    rhs = np.zeros((n, 6)); rhs[:] = np.einsum("nij,nj->ni", Hs[:, :, 3:], Pm)
    if n >= 3:
        h1, h2 = t[1:-1] - t[:-2], t[2:] - t[1:-1]; hm = (h1 + h2) / 2; ok = (h1 > 0) & (h2 > 0) & (h1 <= 0.3) & (h2 <= 0.3)
        c = np.stack([1 / h1, -1 / h1 - 1 / h2, 1 / h2], 1) / hm[:, None] * ok[:, None]            # (n-2, 3): second difference over uneven steps
        Rrel = np.einsum("nji,njk->nik", Rm[:-1], Rm[1:])                                          # R_k^T R_{k+1}
        w = Rot.from_matrix(Rrel).as_rotvec() / (t[1:] - t[:-1])[:, None]
        e = (w[1:] - w[:-1]) / hm[:, None] / BA_ALPHA * ok[:, None]                                # angular acceleration in answer k's axes
        ne = np.linalg.norm(e, axis=1); sw = np.where(ne > 3.0, np.sqrt(3.0 / np.maximum(ne, 1e-12)), 1.0)   # Huber at 3 sigma
        m = n - 2; J = np.zeros((m, 6, n, 6)); r = np.zeros((m, 6)); j = np.arange(m); I3 = np.eye(3)
        ca = c / BA_ALPHA * sw[:, None]
        J[j, :3, j, :3] = ca[:, 0, None, None] * np.transpose(Rrel[:-1], (0, 2, 1))                # R_k^T R_{k-1}
        J[j, :3, j + 1, :3] = ca[:, 1, None, None] * I3
        J[j, :3, j + 2, :3] = ca[:, 2, None, None] * Rrel[1:]
        r[:, :3] = e * sw[:, None]
        for a in range(3): J[j, 3:, j + a, 3:] = (c[:, a] / BA_ACC)[:, None, None] * I3            # linear acceleration (residual 0 at the unknowns)
        Jm = J.reshape(6 * m, N); M += Jm.T @ Jm; rhs -= (Jm.T @ r.reshape(-1)).reshape(n, 6)
    x = np.linalg.solve(M + 1e-9 * np.eye(N), rhs.reshape(-1)).reshape(n, 6)
    return (Rot.from_matrix(Rm) * Rot.from_rotvec(x[:, :3])).as_quat(), x[:, 3:]
def predict(hist, i):                               # constant velocity from the last few solved frames (frame, Rot, pos)
    if len(hist) < 2: return hist[-1][1], hist[-1][2]
    fi = np.array([h[0] for h in hist], float); vel = np.polyfit(fi, np.array([h[2] for h in hist]), 1)[0]
    w = np.mean([(a[1].inv() * b[1]).as_rotvec() / (b[0] - a[0]) for a, b in zip(hist[:-1], hist[1:])], axis=0)
    return hist[-1][1] * Rot.from_rotvec(w * (i - hist[-1][0])), hist[-1][2] + vel * (i - hist[-1][0])
def plausible(r, pred, dt):                         # dt: seconds since the last answer (3 m + 60 m/s, 20 deg + 600 deg/s)
    return r is not None and r[2] >= MIN_INL and np.linalg.norm(r[1] - pred[1]) < 3.0 + 60 * dt and np.degrees((r[0].inv() * pred[0]).magnitude()) < 20 + 600 * dt
pool = ThreadPoolExecutor(max(4, len(streams)))
# ---- a cycle over the streams that have a frame: XFeat (GPU) -> plan + LighterGlue (GPU) -> PnP (CPU threads).
# PIPE overlaps the next cycle's GPU work with this cycle's PnP: 1 = only the next XFeat (the plan still sees every
# answer), 2 = XFeat and matching (the next plan predicts from answers one cycle older). 0 = one after another.
# LOST is time, not attempts: a faster tracker makes more attempts in the same gap, and giving up sooner sends it to the
# slow global retrieval, which drops frames and loses it again
# d05 alone: PIPE 0 28 Hz, 1 43 Hz, 2 56 Hz, the drawn position no worse (0.61 / 0.58 / 0.55 m p50); 4 streams at once
# (3 race flights at 30 fps + d05): 2 gives 25-32 Hz each with the GPU busy all the time (XFeat 8 + LighterGlue 19 ms; PyTorch + MAGSAC)
PIPE = int(E("PIPE", 1 if RENDER else 2))         # with RENDER, 1: the prediction (and so the render) one cycle fresher; 4 at once, the drawn p90 KNT 5.2 -> 3.1 m
def flowing(s):                                     # carry this stream's points with LK instead of matching (FLOW, below)
    return bool(FLOW and s.hist and s.flow is not None and len(s.flow["X"]) >= FLOW_RESEED and s.flow["age"] < FLOW_EVERY)
def front(batch):                                   # GPU: features, for the streams that will be matched
    t = time.perf_counter(); fl = [flowing(s) for s, _ in batch]; sub = [b for b in range(len(batch)) if not fl[b]]
    q, x, row = [None] * len(batch), None, {b: k for k, b in enumerate(sub)}
    if sub:
        qs, x = features([batch[b] for b in sub])
        for b, qb in zip(sub, qs): q[b] = qb
    sync(); return dict(batch=batch, q=q, x=x, row=row, fl=fl, feat=time.perf_counter() - t)
def middle(c):                                      # plan from the answers so far, relocalization retrieval, matching (GPU)
    batch, q, x = c["batch"], c["q"], c["x"]; t = time.perf_counter(); plan, lost = [], []
    for b, (s, i) in enumerate(batch):
        if c["fl"][b]:
            plan.append(("flow", None, [], None))  # no XFeat, no matching: the CPU carries last frame's points over with LK
        elif s.hist and (i - s.hist[-1][0]) / s.fps <= LOST:
            pred = predict(s.hist, i)
            # with RENDER the render at the prediction replaces the keyframes, except right after a failed try, when the keyframes are
            # matched too: a bad prediction renders a view that barely overlaps the frame, and the keyframes' wider pick still finds it
            ks = [] if scene and not (s.fails and RFALL) else s.nearest(*pred, NEAR_K, NEAR_ANG, 30) if NEAR and s.fails else s.nearest(*pred)
            plan.append(("track", pred, ks, s.hist[-1][0]))
        else: plan.append(("reloc", None, None, None)); lost.append(b)
    tr = [b for b, p in enumerate(plan) if p[0] == "track"] if scene else []
    if tr:                                          # one render per tracked stream, at its prediction
        tv = time.perf_counter()
        for b, v in zip(tr, scene.views([plan[b][1] for b in tr], [batch[b][0].K for b in tr])): plan[b][2].append(v)
        c["rend"] = time.perf_counter() - tv
    if lost:
        with torch.no_grad(): g = torch.nn.functional.normalize(dino(((torch.nn.functional.interpolate(x[[c["row"][b] for b in lost]], (168, 224), mode="area") if dev == "cuda" else   # MPS: area only for divisible sizes
                                                                  torch.nn.functional.interpolate(x[[c["row"][b] for b in lost]], (168, 224), mode="bilinear", antialias=True)) - MEAN) / STD), dim=1)
        for b, gb in zip(lost, g):
            s, i = batch[b]; sim = s.m["g"] @ gb
            if REACH and s.last:                    # only keyframes it could have reached since it was last seen
                far = np.linalg.norm(s.m["pos"] - s.last[2], axis=1) > 5 + REACH * (i - s.last[0]) / s.fps
                if (~far).sum() >= RTOPK: sim = sim.masked_fill(torch.from_numpy(far).to(sim.device), -2)
            ks = torch.topk(sim, RTOPK).indices.tolist()
            if NEAR and s.last:                     # where it was last seen, looking any way
                ks += [k for k in s.nearest(s.last[1], s.last[2], NEAR_K, 180, 60) if k not in ks]
            plan[b] = ("reloc", None, ks, None)
    pairs = [(q[b], batch[b][0], k) for b in range(len(batch)) for k in plan[b][2]]
    tg = time.perf_counter(); res = match_all(pairs); sync(); c["glue"] = time.perf_counter() - tg; per, n = [], 0
    for b in range(len(batch)): per.append(res[n:n + len(plan[b][2])]); n += len(plan[b][2])
    c.update(plan=plan, per=per, pairs=len(pairs), match=time.perf_counter() - t); return c
# FLOW: after an answer, its inlier points (where they are in the frame, where they are in the world) are carried to the
# next frame by pyramidal LK with a forward-backward check and solved again by PnP - no XFeat matching, no GPU. The
# points thin out; below FLOW_RESEED, or after FLOW_EVERY carried frames, the next frame is matched again.
FLOW_FB, FLOW_WIN = E("FLOW_FB", 1.0), int(E("FLOW_WIN", 21))   # forward-backward tolerance (px), LK window
FLOW_SIG = E("FLOW_SIG", 1.0)                       # carried answers are coarser: their sigma in the drawn filter and trail x this
LK = dict(winSize=(FLOW_WIN, FLOW_WIN), maxLevel=4, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))
def gray(s, i): return cv2.cvtColor(s.frames[i], cv2.COLOR_RGB2GRAY)
def carry(s, i, uv, X, r, age):                    # the flow state an answer leaves behind
    return dict(gray=gray(s, i), uv=np.ascontiguousarray(uv[r[3]], np.float32), X=X[r[3]], age=age) if FLOW else None
def solve(c, b):                                    # CPU: PnP for one stream -> (answer, how, finish time, new flow state or "drop")
    (s, i), (mode, pred, _, last), per = c["batch"][b], c["plan"][b], c["per"][b]
    if mode == "flow":
        f = s.flow                                  # as the previous cycle left it (it has landed by now)
        if f is None or not s.hist: return None, None, time.perf_counter(), "drop"
        g = gray(s, i); p1, st, _ = cv2.calcOpticalFlowPyrLK(f["gray"], g, f["uv"], None, **LK)
        p0, st2, _ = cv2.calcOpticalFlowPyrLK(g, f["gray"], p1, None, **LK)
        ok = (st[:, 0] == 1) & (st2[:, 0] == 1) & (np.linalg.norm(p0 - f["uv"], axis=1) < FLOW_FB)
        uv, X = p1[ok], f["X"][ok]; r = pnp(s.K, uv, X) if len(uv) >= FLOW_MIN else None
        if r is not None and r[2] >= FLOW_MIN and plausible(r, predict(s.hist, i), (i - s.hist[-1][0]) / s.fps):
            return r, "flow", time.perf_counter(), dict(gray=g, uv=np.ascontiguousarray(uv[r[3]], np.float32), X=X[r[3]], age=f["age"] + 1)
        return None, None, time.perf_counter(), "drop"
    if mode == "track":
        uv, X = (np.concatenate([u for u, _ in per]), np.concatenate([X for _, X in per])) if per else (None, None)
        r = pnp(s.K, uv, X) if per else None
        return (r, "track", time.perf_counter(), carry(s, i, uv, X, r, 0)) if plausible(r, pred, (i - last) / s.fps) else (None, None, time.perf_counter(), "drop")
    best = None
    for uv, X in per:
        r = pnp(s.K, uv, X)
        if r and (best is None or r[2] > best[0][2]): best = (r, uv, X)
    return (best[0], "reloc", time.perf_counter(), carry(s, i, best[1], best[2], best[0], 0)) if best and best[0][2] >= RMIN else (None, None, time.perf_counter(), "drop")
def land(c, out, t0, clock0):                       # apply the answers to the streams and record them
    for b, ((s, i), (r, how, tf, fs)) in enumerate(zip(c["batch"], out)):
        done = (tf - s.t_base) if s.live else clock0 + (tf - t0)   # live: on the cell's own time line
        s.flow = None if fs == "drop" else fs if fs is not None else s.flow
        s.fails = 0 if r is not None else s.fails + 1
        if r is not None:
            if how == "reloc": s.hist = []
            s.hist = (s.hist + [(i, r[0], r[1])])[-6:]; s.last = s.hist[-1]
        lat = dict(lat=round((tf - s.arrive[i]) * 1000, 1), e2e=round((time.time() - (time.perf_counter() - tf) - SEND_T0 - i / s.fps) * 1000, 1) if SEND_T0 else None) if s.live else {}
        win = {}
        if BA and r is not None and r[4] is not None:
            t = (s.arrive[i] - s.t_base) if s.live else i / s.fps   # when the frame was taken, as near as this side knows
            s.win = [w for w in getattr(s, "win", []) if w[0] > t - BA and w[1] < i] + [(t, i, r[0], r[1], r[4], r[0].as_matrix())]
            if len(s.win) >= 3:
                qs, ps = smooth(s.win); win = {"win": [[w[1], *p_, *q_] for w, p_, q_ in zip(s.win, np.round(ps, 4).tolist(), np.round(qs, 5).tolist())]}
        s.recs.append(dict(i=i, done=done, plan=c["plan"][b][0], **lat, **win, **(dict(pos=r[1].tolist(), quat=r[0].as_quat().tolist(), inl=int(r[2]), how=how, **({"H": [float(f"{v:.6g}") for v in r[4][np.triu_indices(6)]]} if INFO and r[4] is not None else {})) if r else {"how": "none"})))
        if subs:
            m = json.dumps(dict(stream=os.path.basename(s.prefix), fps=s.fps, **s.recs[-1]))
            for q in list(subs):
                if not q.full(): q.put_nowait(m)
    s_ = {k: c[k] * 1000 for k in ("feat", "match", "rend", "glue") if k in c}; cycles.append(dict(n=len(c["batch"]), pairs=c["pairs"], **s_))
REAL = any(s.live for s in streams)
# PACE=1 (recorded video): wait on the wall clock for the next frame instead of jumping to it, so the GPU idles between
# frames as it does on a live feed. Without it a recording runs back to back - right for timings, wrong for power.
PACE = int(E("PACE", 0))
def pick(clock):                                    # the newest arrived frame of each stream not already in flight
    if REAL:
        batch = [(s, s.N - 1) for s in streams if s.N - 1 >= s.next]
        for s, i in batch: s.next = i + 1
        return batch, [s for s in streams if not s.src.ended or s.next < s.N]
    live = [s for s in streams if s.next < s.N]
    if LIVE: ready = [s for s in live if s.next / s.fps <= clock + 1e-9]; batch = [(s, max(s.next, min(s.N - 1, int(math.floor(clock * s.fps + 1e-9))))) for s in ready]
    else: batch = [(s, s.next) for s in live]
    for s, i in batch: s.next = i + 1
    return batch, live
cycles = []
for _ in range(3):                                  # warm up the kernels; the state it leaves is thrown away
    c = middle(front([(s, 0) for s in streams])); [solve(c, b) for b in range(len(streams))]
    for s in streams: s.hist, s.flow, s.fails, s.last = [], None, 0, None
if REAL:                                            # the warm-up's blank frame is not a frame of the source
    for s in streams: s.frames, s.N, s.next = {}, 0, 0
    for src in sources.values(): src.start()
    print("listening", flush=True)
else: print("tracking", flush=True)
cycles.clear(); clock, t_all, pending, steps = 0.0, time.time(), None, []
def loop():
  global clock, pending
  while True:
      t0, clock0 = time.perf_counter(), clock
      futs = [pool.submit(solve, pending, b) for b in range(len(pending["batch"]))] if pending else None
      batch, live = pick(clock)
      if not batch and not futs:
          if not live: break
          if REAL: time.sleep(0.001); continue
          nxt_t = min(s.next / s.fps for s in live)
          if PACE: time.sleep(max(0.0, nxt_t - clock))
          clock = nxt_t; continue
      nxt = None
      if PIPE == 0 and futs is None and batch:        # one after another: this batch's whole cycle now
          nxt = middle(front(batch)); futs = [pool.submit(solve, nxt, b) for b in range(len(batch))]; pending, nxt = nxt, None
      elif batch:
          nxt = front(batch)
          if PIPE >= 2: nxt = middle(nxt)
      if futs is not None:
          tw = time.perf_counter(); out = [f.result() for f in futs]; wait = time.perf_counter() - tw
          land(pending, out, t0, clock0); cycles[-1]["wait"] = wait * 1000
      if nxt is not None and PIPE == 1: nxt = middle(nxt)
      pending = nxt; clock = clock0 + (time.perf_counter() - t0); steps.append((time.perf_counter() - t0) * 1000)
      if len(steps) % 500 == 0: print(f"{len(steps)} steps, {(time.time() - t_all) / len(steps) * 1000:.1f} ms each", flush=True)
# a background job of a non-interactive shell starts with SIGINT ignored, and Python then leaves it ignored: a live run started
# that way never stopped. TERM (kill, systemd) also ends the loop and writes the summary.
import signal; signal.signal(signal.SIGINT, signal.default_int_handler); signal.signal(signal.SIGTERM, signal.default_int_handler)
try: loop()
except KeyboardInterrupt: print("stopped", flush=True)   # a live feed never ends by itself; keep what was measured
pc = lambda a, q: float(np.percentile(a, q)) if len(a) else float("nan")
print(f"PIPE {PIPE}: steps {len(steps)}, ms p50 {pc(steps, 50):.1f} p90 {pc(steps, 90):.1f}; streams per cycle p50 {pc([c['n'] for c in cycles], 50):.0f}, pairs p50 {pc([c['pairs'] for c in cycles], 50):.0f}; " +
      ", ".join(f"{k} p50 {pc([c[k] for c in cycles if k in c], 50):.1f}" for k in ("feat", "match", "rend", "glue", "wait")) + " (match includes rend, the renders, and glue, the matcher; wait: for the PnP threads after the GPU work)")
# ---- per stream: the extrapolated dot at frame j, from the answers finished by j/fps (scoring only; the page draws its own). Rotation: the newest answer
# carried at constant angular velocity. Position: a constant-acceleration Kalman filter over the answers (measurement
# sigma 0.25 m at 200 inliers, larger with fewer), then a new answer's jump is spread over BLEND instead of drawn at once.
# The answers are what wobbles (0.25 m each at ~20 Hz), not the extrapolation: capping the carried speed at the recent
# speed changed nothing. d05, tried on the answer log: last answer + velocity 0.86 m p50 / 17.7 cm wobble (distance to a
# quadratic over 0.25 s); Kalman 0.60 / 15.2; Kalman + BLEND 1.5 frames 0.70 / 11.1; BLEND 3 frames 0.85 / 9.7.
def Fm(dt): return np.array([[1, dt, dt * dt / 2], [0, 1, dt], [0, 0, 1]])
def Qm(dt): return KQ * np.array([[dt**5 / 20, dt**4 / 8, dt**3 / 6], [dt**4 / 8, dt**3 / 3, dt**2 / 2], [dt**3 / 6, dt**2 / 2, dt]])
FRAME = "web: x east, y up, z south, metres; quat (x,y,z,w) world-from-camera, COLMAP camera axes"
def finish(s):
    N, fps, cam = s.N, s.fps, s.cam
    if not N: print(f"== {s.prefix}: no frames arrived"); return
    with open(s.prefix + ".jsonl", "w") as f:
        for r in s.recs: f.write(json.dumps(r) + "\n")
    sol = [r for r in s.recs if r["how"] != "none"]; drawn, k, h = [], 0, []; kx = kP = kf = None; off, last = np.zeros(3), None
    for j in range(N):
        new = False
        while k < len(sol) and (sol[k]["done"] <= j / fps + 1e-9 if LIVE else sol[k]["i"] <= j):
            s_ = sol[k]; reset = s_["how"] == "reloc" or not h; h = ([] if reset else h)[-5:] + [(s_["i"], Rot.from_quat(s_["quat"]), np.array(s_["pos"]))]; k += 1; new = True
            z, r = np.array(s_["pos"]), 0.25 * math.sqrt(200 / max(s_["inl"], 30)) * (FLOW_SIG if s_["how"] == "flow" else 1)
            if reset or kx is None: kx, kP, kf = np.stack([z, np.zeros(3), np.zeros(3)]), np.diag([r * r, 100, 1000.]), s_["i"]
            else:
                dt = (s_["i"] - kf) / fps; A = Fm(dt); x = A @ kx; P = A @ kP @ A.T + Qm(dt); G = P[:, 0] / (P[0, 0] + r * r)
                kx, kP, kf = x + np.outer(G, z - x[0]), P - np.outer(G, P[0]), s_["i"]
        if not h: drawn.append(None); continue
        age = (j - h[-1][0]) / fps
        if age > 0.5: drawn.append((h[-1][1], kx[0], age)); last = None; continue   # past 0.5 s without an answer, stop extrapolating
        p = kx[0] + kx[1] * (j - kf) / fps
        if new and last is not None: off = last - p
        off = off * math.exp(-1 / (BLEND * fps)) if BLEND > 0 else off * 0; last = p + off
        drawn.append((predict(h, j)[0], last, age))
    first = next((j for j, d in enumerate(drawn) if d), N)
    out = []                                        # the viewer indexes poses by frame, so every frame gets one
    for j in range(N if first < N else 0):
        R, p, age = drawn[max(j, first)]
        out.append({"i": j, "t": round(cam["t0"] + j / fps, 4), "pos": np.round(p, 3).tolist(), "quat": np.round(R.as_quat(), 6).tolist(),
                    # live, the newest answer is always a few frames old (latency), so "fresh" means within FRESH
                    "src": "rt-wait" if j < first else "rt" if age <= FRESH else "rt-carry"})
    json.dump({"frame": FRAME, "t0": cam["t0"], "fps": fps, "poses": out}, open(s.prefix + ".json", "w"))
    # the trail behind the live dot can be redrawn: frame j is drawn once the answers up to TRAIL_L later are in - a
    # quadratic over the answers within TRAIL_W, weighted by inliers; rotation slerped between the answers around it.
    # d05, on the answer log: 200 ms late -> 0.28 m p50 / 1.8 cm wobble (the live dot: 0.71 m / 11 cm).
    trail = []
    if sol:
        fi = np.array([r["i"] for r in sol]); dn = np.array([r["done"] if LIVE else r["i"] / fps for r in sol]); AP = np.array([r["pos"] for r in sol])
        wt = np.sqrt(np.minimum([r["inl"] for r in sol], 400) / 200) / np.array([FLOW_SIG if r["how"] == "flow" else 1 for r in sol]); L, Wf = TRAIL_L * fps, TRAIL_W * fps
        for j in range(N):
            got = dn <= (j + L) / fps + 1e-9; m = got & (np.abs(fi - j) <= Wf)
            ga = np.nonzero(got & (fi <= j))[0]; gb = np.nonzero(got & (fi >= j))[0]   # only answers that have arrived by then
            if m.sum() < 4 or not len(ga) or not len(gb): trail.append(None); continue
            a, b = ga[-1], gb[0]
            if fi[b] - fi[a] > 2 * Wf: trail.append(None); continue
            p = np.polyfit(fi[m] - j, AP[m], 2, w=wt[m])[-1]
            R = Rot.from_quat(sol[a]["quat"]) if a == b else Slerp([fi[a], fi[b]], Rot.from_quat([sol[a]["quat"], sol[b]["quat"]]))([j])[0]
            trail.append((R, p))
    else: trail = [None] * N
    json.dump({"frame": FRAME, "t0": cam["t0"], "fps": fps,
               "poses": [{"i": j, "t": round(cam["t0"] + j / fps, 4), **({"pos": np.round(trail[j][1], 3).tolist(), "quat": np.round(trail[j][0].as_quat(), 6).tolist(), "src": "rt"}
                         if trail[j] else {"pos": o["pos"], "quat": o["quat"], "src": "rt-carry" if o["src"] == "rt" else o["src"]})} for j, o in enumerate(out)]},
              open(s.prefix + "_trail.json", "w"))
    # summary
    dur = N / fps
    print(f"== {s.prefix}: processed {len(s.recs)} of {N} frames ({len(s.recs) / dur:.1f} Hz), solved {len(sol)} ({len(sol) / dur:.1f} Hz, {sum(r['how'] == 'reloc' for r in sol)} by relocalizing)")
    if LIVE and sol:
        lat = np.array([r["done"] - r["i"] / fps for r in sol]) * 1000; print(f"latency ms (frame arrives -> pose): p50 {pc(lat, 50):.1f} p90 {pc(lat, 90):.1f}")
    print(f"first drawn frame #{first}")
    if s.live:
        lat = [r["lat"] for r in s.recs]; e2e = [r["e2e"] for r in s.recs if r.get("e2e") is not None]
        net = [(s.arrive_wall[i] - SEND_T0 - i / fps) * 1000 for i in s.arrive_wall] if SEND_T0 else []
        print(f"live: {N} frames arrived; arrival -> pose ms p50 {pc(lat, 50):.1f} p90 {pc(lat, 90):.1f}" +
              (f"; sent -> arrived ms p50 {pc(net, 50):.1f} p90 {pc(net, 90):.1f}; sent -> pose ms p50 {pc(e2e, 50):.1f} p90 {pc(e2e, 90):.1f}" if SEND_T0 else ""))
    if s.truth:
        tr = {p["i"]: p for p in json.load(open(s.truth))["poses"] if p.get("src") in ("ba", "ba-fill", "takeoff", "ground")}
        def errs(pairs):
            e = [(np.linalg.norm(np.array(p) - tr[j]["pos"]), np.degrees((Rot.from_quat(q).inv() * Rot.from_quat(tr[j]["quat"])).magnitude())) for j, q, p in pairs if j in tr]
            return np.array(e).reshape(-1, 2)
        for name, pairs in (("solved frames (the answer itself)", [(r["i"], r["quat"], r["pos"]) for r in sol]),
                            (f"trail, {TRAIL_L * 1000:.0f} ms late", [(j, t[0].as_quat(), t[1]) for j, t in enumerate(trail) if t]),
                            ("drawn, every frame (latency and drops included)", [(j, d[0].as_quat(), d[1]) for j, d in enumerate(drawn) if d])):
            e = errs(pairs)
            print(f"{name}: n {len(e)} of {len(tr)}; pos m p50 {pc(e[:, 0], 50):.2f} p90 {pc(e[:, 0], 90):.2f}; <1 m {np.mean(e[:, 0] < 1) * 100:.0f}% <2 m {np.mean(e[:, 0] < 2) * 100:.0f}%;"
                  f" rot deg p50 {pc(e[:, 1], 50):.1f} p90 {pc(e[:, 1], 90):.1f}")
        miss = [j for j in tr if j < N and (not drawn[j] or drawn[j][2] > FRESH)]
        print(f"frames with truth but nothing fresh (no answer within FRESH): {len(miss)}")
for s in streams: finish(s)
print("RT-DONE", flush=True)
