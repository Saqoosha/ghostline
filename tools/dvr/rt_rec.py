"""rt_rec.py: records every NDI source on the LAN while any cell of its 2x2 grid shows a picture (rt_signal.py), one file per
stretch: the file is cut once all four cells have been empty for REC_HOLD seconds. Started by rt_control.py, listed on its /rec page.
Each source is watched on its low-bandwidth stream (about 640 wide, five looks a second), so a quiet source costs little on the
battery; only while it is recorded is its full stream received too, and the frame is piped as it comes (BGRX) to ffmpeg. The
file is a fragmented mp4 and stays one (playable up to the last second if the box loses power; not remuxed at the venue,
which would copy every file once more - for a browser that seeks it, later: ffmpeg -i x.mp4 -c copy -movflags +faststart y.mp4). The first ~0.5 s of a stretch is lost: the full stream connects after the picture is seen.
env: REC (1; 0 = off), REC_DIR (rec/ beside this file), REC_FPS (0 = the source's own rate, the Event VRX's 60; 30 thins it),
     REC_RATE (16M: about 7 GB an hour), REC_HOLD (5 s), REC_ENC (h264_nvenc; libx264 without the GPU)"""
import glob, hashlib, json, os, re, subprocess, threading, time, traceback
HERE = os.path.dirname(os.path.abspath(__file__)); E = os.environ.get
REC, REC_DIR, REC_FPS, REC_HOLD, REC_ENC = int(E("REC", 1)), os.path.expanduser(E("REC_DIR", HERE + "/rec")), float(E("REC_FPS", 0)), float(E("REC_HOLD", 5)), E("REC_ENC", "h264_nvenc")
REC_RATE = E("REC_RATE", "16M")
WRITERS = set()                                     # every Writer whose thread is alive: capturing, or closing its file
STOPPING = threading.Event()                        # set by stop_all: a picture still on screen must not start a new file
FILE = re.compile(r"[A-Za-z0-9_.-]+\.mp4")          # what /rec/<file> may serve

def receiver(highest):
    """a receiver kept for the life of its watcher: connected and disconnected, never dropped. Dropping one leaves its FrameSync
    to the garbage collector, whose NDIlib_framesync_destroy waited for ever holding the GIL and froze the whole page (macOS, cyndilib)"""
    from cyndilib.receiver import Receiver
    from cyndilib.video_frame import VideoFrameSync
    from cyndilib.wrapper.ndi_recv import RecvColorFormat, RecvBandwidth
    rx = Receiver(color_format=RecvColorFormat.BGRX_BGRA, bandwidth=RecvBandwidth.highest if highest else RecvBandwidth.lowest)
    vf = VideoFrameSync(); rx.frame_sync.set_video_frame(vf); return rx, vf

class Writer:
    """one file: every frame the source sends (thinned by its timestamps to REC_FPS if that is lower), kept to the wall clock: a
    stalled sender has its last frame repeated and frames 2 or more ahead of the clock are dropped, so the file's time is the venue's"""
    def __init__(self, src, stem, rx, vf):
        self.src, self.stem, self.rx, self.vf, self.stop, self.frames, self.t0, self.size = src, stem, rx, vf, threading.Event(), 0, time.time(), None
        self.on = [0, 0, 0, 0]; self.looks = 0; self.failed = False     # how many of the watcher's looks found a picture in each cell
        self.capturing = True                       # until the full stream is let go: then the next stretch may start while this file is closed
        WRITERS.add(self); self.thread = threading.Thread(target=self.run, daemon=True); self.thread.start()
    def run(self):
        rx, vf = self.rx, self.vf; ff = None; mp4 = f"{REC_DIR}/{self.stem}.mp4"
        old = vf.get_timestamp_posix() if vf.xres else None   # the last stretch's last frame, still held: wait for a new one
        rx.set_source(self.src)
        try:
            t = time.time()
            while (not vf.xres or vf.get_timestamp_posix() == old) and time.time() - t < 5 and not self.stop.is_set(): rx.frame_sync.capture_video(); time.sleep(0.01)
            if not vf.xres or vf.get_timestamp_posix() == old: print(f"rec {self.src.name}: no full-stream picture", flush=True); return
            w, h = self.size = vf.get_resolution(); src = float(vf.get_frame_rate()) or 30; fps = min(REC_FPS, src) if REC_FPS else src
            # NVENC takes BGRX and converts it itself: 60 fps 1080p costs ffmpeg 11% of a core instead of 63% (rt4090), the same yuv420p
            enc = ["-c:v", REC_ENC] + (["-preset", "p4", "-b:v", REC_RATE] if "nvenc" in REC_ENC else ["-preset", "veryfast", "-b:v", REC_RATE, "-pix_fmt", "yuv420p"])
            ff = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr0", "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-",
                                   *enc, "-g", f"{round(fps * 2)}", "-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-y", mp4],
                                  stdin=subprocess.PIPE)
            self.t0 = time.time(); self.meta(fps=fps, live=True)
            print(f"rec {self.src.name}: {self.stem}.mp4 ({w}x{h}, {fps:g} fps, {REC_ENC})", flush=True)
            last = kept = None
            while not self.stop.is_set():
                rx.frame_sync.capture_video()
                if vf.get_resolution() != (w, h): print(f"rec {self.src.name}: the size changed, cut", flush=True); break
                ts, clock = vf.get_timestamp_posix(), (time.time() - self.t0) * fps   # clock: the frames the wall clock has had
                if ts != last:                      # a new frame (polled every 2 ms, as rt_track.py's NDI reader)
                    last = ts
                    if not (kept is not None and 0 <= ts - kept < 0.9 / fps) and self.frames < clock + 2:   # not thinned out, not ahead
                        kept = ts; ff.stdin.write(vf.get_array()); self.frames += 1
                elif self.frames < clock - 2:       # the sender stalled: repeat its last frame up to the clock
                    buf = vf.get_array()
                    while self.frames < clock: ff.stdin.write(buf); self.frames += 1
                time.sleep(0.002)
        except Exception: traceback.print_exc(); self.failed = ff is None   # (ffmpeg not started: wait before trying again)
        finally:
            try:
                rx.disconnect()
                if ff:
                    try: ff.stdin.close()
                    except Exception: pass
                    ff.wait()
                    self.failed = bool(ff.returncode)
                    if self.failed: print(f"rec {self.src.name}: ffmpeg failed (exit {ff.returncode}, its message above)", flush=True)
            finally: self.capturing = False
            try:
                if ff and (not os.path.exists(mp4) or not os.path.getsize(mp4)):   # nothing written: no file to list (a non-empty one that broke is kept)
                    for f in (mp4, f"{REC_DIR}/{self.stem}.json"): os.path.exists(f) and os.remove(f)
                elif ff: self.finish(mp4, fps)
            finally: WRITERS.discard(self)
    def finish(self, mp4, fps):
        self.meta(live=False); print(f"rec {self.src.name}: {self.stem}.mp4 cut, {self.frames / fps:.0f} s", flush=True)
    def meta(self, **kw):                           # beside the mp4: what the list shows
        f = f"{REC_DIR}/{self.stem}.json"
        try: m = json.load(open(f))
        except Exception: m = dict(source=self.src.name, start=self.t0, size=self.size, enc=REC_ENC)
        m.update(kw, start=self.t0, frames=self.frames, cells=[round(n / self.looks, 3) if self.looks else None for n in self.on])
        with open(f + ".tmp", "w") as o: json.dump(m, o, ensure_ascii=False)
        os.replace(f + ".tmp", f)

class Watcher:
    """one NDI source: its low-bandwidth stream looked at five times a second. A picture in any cell starts a Writer; all four
    empty (or no frames) for REC_HOLD seconds, or the source leaving, stops it"""
    def __init__(self, finder, name):
        self.finder, self.name, self.writer, self.cells, self.began = finder, name, None, None, 0.0
        threading.Thread(target=self.run, daemon=True).start()
    def run(self):
        import numpy as np
        from rt_signal import cells_on
        rx, vf = receiver(False); self.full = receiver(True)
        while True:
            src = next((s for s in self.finder.iter_sources() if s.name == self.name), None)
            if src is None: time.sleep(2); continue
            rx.set_source(src); last_on, last_ts, seen = 0.0, None, 0.0
            try:
                while self.name in self.finder.get_source_names():
                    rx.frame_sync.capture_video()
                    ts = vf.get_timestamp_posix() if vf.xres else None
                    if ts and ts != last_ts:
                        last_ts, seen = ts, time.time(); w, h = vf.get_resolution(); self.cells = cells_on(np.asarray(vf.get_array()).reshape(h, w, 4))
                    elif time.time() - seen > 2: self.cells = None   # no new frame for 2 s (by this box's clock: the sender's may differ)
                    now = time.time(); wr = self.writer
                    if self.cells and any(self.cells): last_on = now
                    if wr and wr.capturing:
                        if self.cells: wr.looks += 1; wr.on = [a + b for a, b in zip(wr.on, self.cells)]
                        if now - last_on > REC_HOLD: wr.stop.set()
                    elif self.cells and any(self.cells) and not STOPPING.is_set() and now - self.began > (60 if wr and wr.failed else 10):   # a failing ffmpeg: once a minute
                        self.began = now; self.writer = Writer(src, time.strftime("%Y%m%d-%H%M%S") + "_" + (re.sub(r"[^A-Za-z0-9_-]+", "-", self.name).strip("-")[:48] or "ndi")
                                             + "-" + hashlib.sha1(self.name.encode()).hexdigest()[:6], *self.full)   # two names that clean up alike stay apart
                    time.sleep(0.2)
            except Exception: traceback.print_exc(); time.sleep(2)
            finally:
                rx.disconnect(); self.cells = None
                if self.writer: self.writer.stop.set()   # the source went away (or the loop failed): cut now, nothing else would

class Recorders:
    def __init__(self, finder):
        self.finder, self.watch = finder, {}
        if REC and finder is not None: os.makedirs(REC_DIR, exist_ok=True); threading.Thread(target=self.run, daemon=True).start()
    def run(self):                                  # every source that shows up gets a watcher, kept for good (sources come back)
        while True:
            try:
                for n in self.finder.get_source_names():
                    if n not in self.watch: self.watch[n] = Watcher(self.finder, n); print(f"rec: watching {n}", flush=True)
            except Exception: traceback.print_exc()
            time.sleep(3)
    def stop_all(self):                             # the service stops: cut every file and wait for it to be closed
        STOPPING.set(); ws = list(WRITERS)
        for wr in ws: wr.stop.set()
        for wr in ws: wr.thread.join(30)
    def delete(self, name):                         # the list page's delete: a finished recording and its .json
        if not isinstance(name, str) or not FILE.fullmatch(name) or not os.path.exists(f"{REC_DIR}/{name}"): raise ValueError("録画が見つからない")
        if any(wr.stem + ".mp4" == name for wr in list(WRITERS)): raise ValueError("録画中は消せない")
        for f in (f"{REC_DIR}/{name}", f"{REC_DIR}/{name[:-4]}.json"):
            if os.path.exists(f): os.remove(f)
    def recording(self): return sum(1 for wr in list(WRITERS) if wr.capturing)   # the control page's count
    def state(self):
        live = {wr.stem: wr for wr in list(WRITERS)}   # capturing or closing: listed as live, not playable or deletable yet
        out = []
        for f in sorted(glob.glob(REC_DIR + "/*.json"), reverse=True):
            stem = os.path.basename(f)[:-5]; mp4 = f"{REC_DIR}/{stem}.mp4"
            if not os.path.exists(mp4): continue
            try: m = json.load(open(f))
            except Exception: continue
            wr = live.get(stem); fps = m.get("fps") or REC_FPS or 30
            out.append(dict(file=stem + ".mp4", source=m.get("source"), start=m.get("start"), size=m.get("size"), bytes=os.path.getsize(mp4),
                            dur=round((wr.frames if wr else m.get("frames", 0)) / fps, 1), live=bool(wr), finishing=bool(wr and not wr.capturing),
                            cells=[round(n / wr.looks, 3) if wr.looks else None for n in wr.on] if wr else m.get("cells")))
        try: st = os.statvfs(REC_DIR); free = st.f_bavail * st.f_frsize
        except Exception: free = None
        return dict(on=bool(REC) and self.finder is not None, dir=REC_DIR, free=free, recs=out,
                    sources={n: dict(cells=w.cells, rec=bool(w.writer and w.writer.capturing)) for n, w in list(self.watch.items())})
