# rt_ndiview.py: the control page's 3D page (rt_control.py /view/, viewer/live.html) as an NDI source and/or an SRT stream, for
# the venue's switcher. Headless Chrome draws it on the GPU (ANGLE over EGL: the default and Vulkan fall back to SwiftShader or crash here,
# and there is no X), its screencast hands each painted frame over as a JPEG (CDP over --remote-debugging-pipe: stdlib only),
# and the sender is usb_ndi.py's, unclocked (the page sets the pace: FPS). The page is opened with ?ndi=1: no panel, and the
# camera is the control page's. A frame is sent again after 0.5 s without a paint, so a still page does not look dead.
# SRT: the JPEGs as they are into ffmpeg, H.264 on NVENC, MPEG-TS, listening (one caller at a time; OBS: a Media Source with
# srt://<rt4090>:9000?mode=caller, format mpegts). ffmpeg is started again whenever its caller leaves.
# The output, its size and rate come from OUTCONF (~/.ghostline-out.json, written by the control page: {"out": "ndi" | "srt" |
# "ndi,srt" | "off", "w", "h", "fps"}), over the env below. When the file changes this exits, and the service starts it again
# with the new one; "off" waits for a change without starting Chrome.
# usage (rt4090, mastenv for cv2 and cyndilib): python rt_ndiview.py      env: URL (http://localhost:8080/view/?ndi=1), NAME (ghostline 3D),
#        OUT (ndi, srt, ndi,srt or off), W, H (1920, 1080), FPS (60; below that the page itself draws less often: ?fps=),
#        SRT (srt://0.0.0.0:9000?mode=listener&latency=120000, latency in µs), BITRATE (6M), Q (the JPEG quality, 90),
#        STATS (seconds between the fps lines in the log, 60)
# Exits when Chrome or the page goes away, the page does not load, or nothing is painted for 10 s (the service restarts it).
import json, os, subprocess, sys, threading, time, base64, cv2, numpy as np
from fractions import Fraction
E = os.environ.get
OUTCONF = os.path.expanduser(E("OUTCONF", "~/.ghostline-out.json"))
mtime = lambda: os.stat(OUTCONF).st_mtime if os.path.exists(OUTCONF) else 0
conf_at = mtime()
try: conf = json.load(open(OUTCONF))
except FileNotFoundError: conf = {}
except Exception as e: print(f"{OUTCONF}: {e}, the env's settings instead", flush=True); conf = {}
URL, NAME, Q = E("URL", "http://localhost:8080/view/?ndi=1"), E("NAME", "ghostline 3D"), int(E("Q", 90))
W, H, FPS = int(conf.get("w", E("W", 1920))), int(conf.get("h", E("H", 1080))), int(conf.get("fps", E("FPS", 60)))
OUT, STATS = set(str(conf.get("out", E("OUT", "ndi"))).split(",")), float(E("STATS", 60))
SRT, BITRATE = E("SRT", "srt://0.0.0.0:9000?mode=listener&latency=120000"), E("BITRATE", "6M")
if not OUT or OUT - {"ndi", "srt", "off"} or ("off" in OUT and len(OUT) > 1): sys.exit(f"the output is ndi, srt, ndi,srt or off, not {','.join(OUT)}")
def watch_conf():                                         # a new setting: exit, and the service starts this again with it
    while mtime() == conf_at: time.sleep(1)
    print("the output's settings changed, starting again", flush=True); os._exit(0)
if "off" in OUT: print("output off", flush=True); watch_conf()
if FPS < 60: URL += ("&" if "?" in URL else "?") + f"fps={FPS}"
if "ndi" in OUT:
    from cyndilib.sender import Sender
    from cyndilib.video_frame import VideoSendFrame
    from cyndilib.wrapper.ndi_structs import FourCC

r0, w0 = os.pipe(); r1, w1 = os.pipe()                    # Chrome reads CDP on fd 3 and writes on fd 4, each message ended by a NUL
def fds(): os.dup2(r0, 3); os.dup2(w1, 4)
chrome = subprocess.Popen(["google-chrome", "--headless=new", "--remote-debugging-pipe", "--use-angle=gl-egl", "--ignore-gpu-blocklist",
                           "--user-data-dir=" + os.path.expanduser("~/.cache/ghostline-ndiview"), "--no-first-run", "--no-default-browser-check",
                           f"--window-size={W},{H}", "--hide-scrollbars", "--mute-audio", "--disable-background-timer-throttling",
                           "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows", "about:blank"],
                          preexec_fn=fds, pass_fds=(3, 4), stdout=subprocess.DEVNULL)   # its stderr goes to the journal
threading.Thread(target=watch_conf, daemon=True).start()   # after the fork: preexec_fn is not safe with threads running
os.close(r0); os.close(w1); out, inp = os.fdopen(w0, "wb", 0), os.fdopen(r1, "rb", 0)
wlock, waits, ids = threading.Lock(), {}, iter(range(1, 1 << 62))
latest, got, painted = [None], threading.Event(), [0]     # the newest JPEG: older ones are dropped if sending falls behind

def write(m):
    with wlock: out.write(json.dumps(m).encode() + b"\0")
def call(method, params=None, sid=None, tries=1):
    for k in range(tries):                                # the screencast is refused while the page is still loading
        i = next(ids); ev = threading.Event(); waits[i] = [ev, None]
        write(dict(id=i, method=method, params=params or {}, **({"sessionId": sid} if sid else {})))
        if not ev.wait(30): sys.exit(f"Chrome did not answer {method}")
        m = waits.pop(i)[1]
        if "error" not in m: return m["result"]
        if k < tries - 1: time.sleep(1)
    sys.exit(f"{method}: {m['error']}")
def read():                                               # any error here ends the process: a dead reader would freeze the feed on its last frame
    try:
        buf = b""
        while True:
            c = inp.read(1 << 20)
            if not c: print("Chrome went away", flush=True); os._exit(1)
            buf += c
            while b"\0" in buf:
                msg, buf = buf.split(b"\0", 1); m = json.loads(msg)
                if m.get("id") in waits: waits[m["id"]][1] = m; waits[m["id"]][0].set()
                elif m.get("method") == "Page.screencastFrame":   # ack at once: Chrome sends the next paint only after the ack
                    write(dict(id=next(ids), method="Page.screencastFrameAck", params=dict(sessionId=m["params"]["sessionId"]), sessionId=m["sessionId"]))
                    latest[0] = m["params"]["data"]; painted[0] += 1; got.set()
                elif m.get("method") in ("Inspector.targetCrashed", "Target.targetDestroyed", "Target.detachedFromTarget"):
                    print(m["method"], flush=True); os._exit(1)
    except Exception as e: print(f"reading Chrome: {type(e).__name__}: {e}", flush=True); os._exit(1)
threading.Thread(target=read, daemon=True).start()

tid = call("Target.createTarget", dict(url="about:blank"))["targetId"]
sid = call("Target.attachToTarget", dict(targetId=tid, flatten=True))["sessionId"]
call("Emulation.setDeviceMetricsOverride", dict(width=W, height=H, deviceScaleFactor=1, mobile=False), sid)
call("Page.enable", sid=sid); call("Inspector.enable", sid=sid)
nav = call("Page.navigate", dict(url=URL), sid)
if nav.get("errorText"): sys.exit(f"{URL}: {nav['errorText']}")    # e.g. the control page not up yet at boot: the service tries again
for _ in range(30):                                       # the live page sets window.ghostline once it runs; a 404 or an error page never does
    if call("Runtime.evaluate", dict(expression="!!window.ghostline", returnByValue=True), sid)["result"].get("value"): break
    time.sleep(1)
else: sys.exit(f"{URL}: the 3D page did not start")
call("Page.startScreencast", dict(format="jpeg", quality=Q, maxWidth=W, maxHeight=H, everyNthFrame=1), sid, tries=30)
print(f"{URL} -> {' and '.join(sorted(OUT))} {W}x{H} {FPS} fps", flush=True)

srt_jpg, srt_got = [None], threading.Event()
def srt():                                                # its own thread: ffmpeg blocks until a caller comes, and NDI must not wait for it
    while True:
        # no probing (its default 5 MB, ~4 s of 720p JPEGs, is held for good) and no mux delay.
        # Not -fflags nobuffer: with the mjpeg demuxer on a pipe it lets almost nothing through
        ff = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-probesize", "32", "-analyzeduration", "0",
                               "-use_wallclock_as_timestamps", "1", "-f", "mjpeg", "-i", "pipe:0",
                               "-fps_mode", "cfr", "-r", str(FPS), "-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll", "-rc", "cbr", "-b:v", BITRATE,
                               "-g", str(FPS), "-bf", "0", "-delay", "0", "-pix_fmt", "yuv420p",
                               "-muxdelay", "0", "-muxpreload", "0", "-flush_packets", "1", "-f", "mpegts", SRT], stdin=subprocess.PIPE)
        print("SRT: waiting for a caller", flush=True)
        try:
            while ff.poll() is None:
                if srt_got.wait(0.5): srt_got.clear()
                if srt_jpg[0]: ff.stdin.write(srt_jpg[0])  # nothing new for 0.5 s: the last frame again
        except (BrokenPipeError, OSError): pass
        ff.kill(); ff.wait(); print(f"SRT: ffmpeg ended ({ff.returncode}: the caller left, or see its lines above), listening again", flush=True); time.sleep(1)
def srt_or_exit():                                        # NDI must not carry on alone when SRT is broken for good (e.g. no ffmpeg)
    try: srt()
    except Exception as e: print(f"SRT: {type(e).__name__}: {e}", flush=True); os._exit(1)
if "srt" in OUT: threading.Thread(target=srt_or_exit, daemon=True).start()

if "ndi" in OUT:
    vf = VideoSendFrame(); vf.set_resolution(W, H); vf.set_frame_rate(Fraction(FPS, 1)); vf.set_fourcc(FourCC.BGRX)
    s = Sender(NAME, clock_video=False); s.set_video_frame(vf); s.open()
# two buffers in turn: the async send reads one while the next frame is decoded into the other (a decode ~7 ms, the send ~4)
bufs = [np.empty((H, W, 4), np.uint8) for _ in range(2)]; t0 = time.time(); n = k = 0; last = 0.0; seen = (t0, 0, 0); paint_at = (t0, 0)
while True:
    if painted[0] != paint_at[1]: paint_at = (time.time(), painted[0])
    elif time.time() - paint_at[0] > 10: sys.exit("nothing painted for 10 s")   # a hung page or GPU: the last frame would go out for ever
    if got.wait(0.5):
        got.clear(); jpg = base64.b64decode(latest[0])
        if "srt" in OUT: srt_jpg[0] = jpg; srt_got.set()
        if "ndi" in OUT:
            f = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            if f is None: continue
            if f.shape[:2] != (H, W): f = cv2.resize(f, (W, H), interpolation=cv2.INTER_LINEAR)
            buf = bufs[k % 2]; k += 1; cv2.cvtColor(f, cv2.COLOR_BGR2BGRA, dst=buf)   # k: decodes only, so a re-send does not flip it
    elif time.time() - last < 0.5 or not n: continue
    if "ndi" in OUT: s.write_video_async(buf.ravel())
    n += 1; last = time.time()
    if last - seen[0] >= STATS:                           # sent, and painted by the page, over the last STATS seconds
        dt = last - seen[0]; print(f"sent {(n - seen[1]) / dt:.1f} fps, painted {(painted[0] - seen[2]) / dt:.1f}", flush=True); seen = (last, n, painted[0])
