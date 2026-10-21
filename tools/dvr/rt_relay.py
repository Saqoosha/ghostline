"""rt_relay.py: the live answers out of the venue, for the audience pages (ghostline.saqoo.sh/<event>/).
Reads the tracker's PUSH (server-sent events, one row per processed frame of a cell) and posts what the pages need to the
Worker's relay (worker/src/room.ts) every 100 ms - one stream from the venue, however many watch. Splits the day into heats
and keeps each heat's answers on disk, uploading them when the heat ends.
  message shapes (worker/src/api.ts): a answer {s, t, p, q, n}; x a frame with a picture and no answer (2 a second per cell
  at most); h a heat's start, again when its names change and every 10 s (a viewer's page and the heat list recover from a
  dropped post); e its end. t: this box's wall clock in ms - an answer's is when its frame reached the tracker, a heat
  start's is the heat's start, and its "at" when it was sent (a delayed page shows new names at that time, not before).
  A heat starts with the first row after none, and ends when no cell has sent a row for 30 s (a cell with no picture sends
  none, rt_track.py SIGNAL=1). The heat manager's number and class do not split heats: FDF showed "7 Expert" for over
  30 minutes. They name the heat - the values while it flew - and go into its id when known at its start: <HHMMSS JST>[-h<n>].
  An answer is held 0.5 s: the tracker's BA revises the last half second ("win"), and the revised pose is sent.
  Posts that fail are dropped, never queued (a heat's end excepted: it goes again with every post until one goes through):
  the tracker is never slowed and the uplink may be a phone. The heat's answers
  are written to <out>/<YYYYMMDD>/<id>.json (every 10 s and at its end; the tracker's own .jsonl is lost when it crashes),
  its start and end to <out>/heats-<YYYYMMDD>.jsonl (the night rebuild reads them), and an upload that failed leaves
  <id>.json.pending, tried again every 30 s and when the relay starts.
usage: python rt_relay.py --push http://127.0.0.1:8765 --base https://ghostline.saqoo.sh --event <day> [--pilots URL] [--out live/relay]
   the key: ~/.config/ghostline/key (RELAY_KEY of the Worker). rt_control.py starts one itself (RELAY_BASE, RELAY_EVENT)."""
import argparse, datetime, glob, gzip, json, os, re, threading, time, urllib.error, urllib.request

JST = datetime.timezone(datetime.timedelta(hours=9))
ORDER = ("R2", "F1", "R4", "R5")                     # the cells tl, tr, bl, br when there is no venue config (FDF's names)
EVENT = re.compile(r"(staging-)?[a-z0-9][a-z0-9-]*")  # as the Worker's
HOLD, X_EVERY, H_EVERY, SAVE_EVERY, RETRY_EVERY, KEEPALIVE = 0.5, 0.5, 10.0, 10.0, 30.0, 5.0
DVR_MAX, DVR_TRIES = 95 << 20, 10                    # a clip's bytes (the Worker takes 100 MB a request), a heat's DVR attempts
ms = lambda t: round(t * 1000)
day = lambda t: datetime.datetime.fromtimestamp(t, JST).strftime("%Y%m%d")


class Heats:
    """where heats start and end, and their names; the clock comes in as arguments"""
    def __init__(self, gap=30.0, order=ORDER):
        self.gap, self.order, self.cur, self.last, self.p = gap, list(order), None, None, {}

    def h(self):
        c = self.cur; return dict(k="h", t=c["t"], id=c["id"], heat=c["heat"], cls=c["cls"], cells=dict(c["cells"]), order=self.order)

    def _named(self):                                # (heat, cls, cells) from the heat manager's last answer (empty after a bad one)
        p = self.p; heat = str(p["heat"]).strip() if p.get("heat") not in (None, "") else None
        return heat or None, (str(p["cls"]).strip() or None) if p.get("cls") not in (None, "") else None, {k: "" if v is None else str(v) for k, v in (p.get("cells") or {}).items()}

    def expire(self, now):                           # first in every entry: a row or names after the gap must not reach the old heat
        if not self.cur or now - self.last <= self.gap: return []
        e = dict(k="e", t=ms(self.last), id=self.cur["id"]); self.cur = None; return [e]

    def row(self, now, stream):
        out = self.expire(now); self.last = now
        if self.cur: return out
        heat, cls, cells = self._named()
        n = re.sub(r"[^A-Za-z0-9_-]", "", heat or "")[:16]   # the Worker's heat id is ASCII: "予選 7" becomes -h7
        hid = datetime.datetime.fromtimestamp(now, JST).strftime("%H%M%S") + (f"-h{n}" if n else "")
        self.cur = dict(id=hid, t=ms(now), heat=heat, cls=cls, cells=cells)
        return out + [self.h()]

    def names(self, now, p):
        out = self.expire(now)
        # unreachable or stale: the heat on keeps its names, but a heat starting later gets none (FDF's afternoon was not heat 7)
        if not isinstance(p, dict) or not isinstance(p.get("cells"), dict) or p.get("stale"): self.p = {}; return out
        self.p = p
        if not self.cur: return out
        heat, cls, cells = self._named()
        if (heat, cls, cells) == (self.cur["heat"], self.cur["cls"], self.cur["cells"]): return out
        self.cur.update(heat=heat, cls=cls, cells=cells); return out + [self.h()]

    tick = expire


class Relay:
    def __init__(self, push_url, base_url, event, key, pilots=None, out_dir="live/relay", post=None, order=ORDER, rec_dir=None, nvenc=False):
        if not EVENT.fullmatch(event or ""): raise ValueError(f"not an event name: {event!r}")
        self.push_url, self.base, self.event, self.key, self.pilots, self.out = push_url, base_url.rstrip("/"), event, key, pilots, out_dir
        self.post = post or self._post
        self.order = order if callable(order) else (lambda o=list(order): o)   # the cells tl, tr, bl, br: the venue's config, read at each heat start
        self.heats = Heats(order=self._order(ORDER)); self.lock = threading.Lock()
        self.held, self.queue, self.last_x, self.rec, self.ends, self.uploads = {}, [], {}, None, [], []
        self.last_h = self.last_save = self.last_retry = self.last_names = 0.0
        self.sent_at = self.tried = None
        self.push_err = self.upload_err = self.bg_err = self.dvr_err = None   # kept apart: a good push must not hide a failing upload
        self.rec_dir, self.nvenc, self.dvr_tried, self.dvr_fail, self.dvr_note = rec_dir, nvenc, {}, None, None   # rec_dir: the venue's recordings (rt_rec.py), for the heats' DVR

    def _order(self, fallback):                      # a fresh box has no config yet (rt_control.py's cell_order raises)
        try: return list(self.order())
        except Exception: return list(fallback)

    # --- the wire
    def request(self, path, body, headers):          # named: Cloudflare refuses urllib's default user agent (403, error 1010)
        return urllib.request.Request(f"{self.base}/{self.event}/api/{path}", data=body, method="PUT" if path.startswith("heats/") else "POST",
                                      headers={"authorization": f"Bearer {self.key}", "content-type": "application/json", "user-agent": "ghostline-relay/1", **headers})

    def _post(self, path, body, headers):
        t = 300 if path.endswith(".mp4") else 10 if path.startswith("heats/") else 2
        with urllib.request.urlopen(self.request(path, body, headers), timeout=t) as f: f.read()

    def send(self, msgs, now):
        self.tried = now
        try: self.post("push", json.dumps(dict(sent=ms(now), msgs=msgs), separators=(",", ":")).encode(), {}); self.sent_at, self.push_err = now, None; return True
        except Exception as e: self.push_err = why(e); return False   # dropped: the next batch goes on

    def status(self, now=None):                      # for the control page: ok while posts go through (an empty one goes every 5 s)
        age = None if self.sent_at is None else (now or time.time()) - self.sent_at
        return dict(ok=age is not None and age < 15 and self.push_err is None, age=age, event=self.event,
                    err=self.push_err or self.upload_err or self.bg_err or self.dvr_err, pending=len(glob.glob(f"{self.out}/*/*.json.pending")),
                    dvr=len(glob.glob(f"{self.out}/*/*.dvr")), dvr_failed=len(glob.glob(f"{self.out}/*/*.dvr.failed")))

    # --- rows in
    def on_row(self, r, now):
        s = r.get("stream")
        if not s: return
        with self.lock:
            if not self.heats.cur or now - self.heats.last > self.heats.gap: self.heats.order = self._order(self.heats.order)   # a heat about to start
            for m in self.heats.row(now, s): self.close(m) if m["k"] == "e" else self.start(m, now)
            for w in r.get("win") or []:              # BA's revisions of answers still held
                a = self.held.get((s, w[0]))
                if a: a["p"], a["q"] = w[1:4], w[4:8]
            t = now - (r.get("lat") or 0) / 1000
            if "pos" in r: self.held[(s, r["i"])] = dict(s=s, t=ms(t), p=r["pos"], q=r["quat"], n=r.get("inl", 0), due=now + HOLD)
            elif now - self.last_x.get(s, -1e9) >= X_EVERY: self.last_x[s] = now; self.queue.append(dict(k="x", s=s, t=ms(t)))

    def on_names(self, p, now):
        with self.lock:
            for m in self.heats.names(now, p):
                if m["k"] == "e": self.close(m); continue
                m["at"] = ms(now); self.queue.append(m); self.log(m); self.last_h = now
            if self.rec and self.heats.cur: self.rec.update(heat=self.heats.cur["heat"], cls=self.heats.cur["cls"], cells=dict(self.heats.cur["cells"]))

    def start(self, h, now):                         # under the lock
        h["at"] = ms(now); self.queue.append(h); self.log(h); self.last_h = now
        self.rec = dict(id=h["id"], start=h["t"], end=None, heat=h["heat"], cls=h["cls"], cells=dict(h["cells"]), order=h["order"], answers={})

    def close(self, e):                              # under the lock: the heat's last answers into its record, its end out, its upload queued
        self.flush_held(); self.queue.append(e); self.log(e)
        if self.rec and self.rec["id"] == e["id"]: self.uploads.append(dict(self.rec, end=e["t"])); self.rec = None

    def log(self, m):
        os.makedirs(self.out, exist_ok=True)
        with open(f"{self.out}/heats-{day(m['t'] / 1000)}.jsonl", "a") as f: f.write(json.dumps(m, ensure_ascii=False) + "\n")

    # --- every 100 ms: the answers that have waited out BA's revisions, then one post
    def flush(self, now):
        with self.lock:
            for key in [k for k, a in self.held.items() if a["due"] <= now]:
                a = self.held.pop(key); p, q = [round(float(v), 3) for v in a["p"]], [round(float(v), 4) for v in a["q"]]
                self.queue.append(dict(k="a", s=a["s"], t=a["t"], p=p, q=q, n=a["n"]))
                if self.rec is not None: self.rec["answers"].setdefault(a["s"], []).append([a["t"], *p, *q, a["n"]])
            msgs, self.queue = self.ends + self.queue, []
        if self.tried is None: self.tried = now
        # an empty post every KEEPALIVE keeps the room (and its backlog) awake
        if msgs or now - self.tried >= KEEPALIVE:
            ok = self.send(msgs, now)
            with self.lock: self.ends = [] if ok else [m for m in msgs if m["k"] == "e"]

    # --- every second: the heat's end, the heat start again, the record on disk. Nothing here waits on the network: uploads
    # and the names are work()'s, on their own thread
    def tick(self, now):
        with self.lock:
            ended = self.heats.tick(now)
            for e in ended: self.close(e)
            # again every 10 s while the heat flies - not after its last row: a start sent then came after its end and revived it
            if not ended and self.heats.cur and now - self.last_h >= H_EVERY and now - self.heats.last < H_EVERY:
                self.queue.append(dict(self.heats.h(), at=ms(now))); self.last_h = now
            rec = self.rec if self.rec and now - self.last_save >= SAVE_EVERY else None
            if rec: self.last_save = now; rec = dict(rec, cells=dict(rec["cells"]), answers={k: list(v) for k, v in rec["answers"].items()})   # the rows are never changed: copying the lists is enough
        if rec: self.save(rec)

    # --- on its own thread: heat uploads, their retries, the heat manager's names (each can take seconds on a bad uplink)
    def work(self, now):
        if self.pilots and now - self.last_names >= 3:
            self.last_names = now
            try: p = self.pilots()
            except Exception: p = {}                 # unreachable: the next heat goes without names
            self.on_names(p, now)
        with self.lock: ups, self.uploads = self.uploads, []
        for rec in ups:                              # each on its own: one failing must not lose the others
            try: self.save(rec)
            except OSError as e:                     # not up before it is on disk (the night rebuild reads it): tried again next time
                self.bg_err = f"save {rec['id']}: {why(e)}"
                with self.lock: self.uploads.append(rec)
                continue
            self.upload(rec)
        if now - self.last_retry >= RETRY_EVERY: self.retry_pending(now)

    def recover(self, now):
        """at start: a heat left open by a restart (its record has no end) is closed at its last answer and goes up"""
        for f in sorted(glob.glob(f"{self.out}/*/*.json")):
            try: rec = json.load(open(f))
            except Exception: continue
            if rec.get("end") is not None or (self.rec and rec["id"] == self.rec["id"]): continue
            rec["end"] = max([rec["start"]] + [r[0] for rows in rec["answers"].values() for r in rows])   # its first answer may be before its start
            e = dict(k="e", t=rec["end"], id=rec["id"])
            self.save(rec); self.log(e)
            with self.lock: self.ends.append(e)
            self.upload(rec)
        self.retry_pending(now)

    def flush_held(self):                            # under the lock: a heat's last answers go into its record before it closes
        for key in list(self.held): self.held[key]["due"] = 0
        held, self.held = self.held, {}
        for a in held.values():
            p, q = [round(float(v), 3) for v in a["p"]], [round(float(v), 4) for v in a["q"]]
            self.queue.append(dict(k="a", s=a["s"], t=a["t"], p=p, q=q, n=a["n"]))
            if self.rec is not None: self.rec["answers"].setdefault(a["s"], []).append([a["t"], *p, *q, a["n"]])

    # --- the heat's answers on disk and up
    def path(self, rec): return f"{self.out}/{day(rec['start'] / 1000)}/{rec['id']}.json"

    def save(self, rec):
        f = self.path(rec); os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f + ".tmp", "w") as o: json.dump(rec, o, separators=(",", ":"), ensure_ascii=False)
        os.replace(f + ".tmp", f)

    def upload(self, rec):
        entry = dict(id=rec["id"], start=rec["start"], end=rec["end"], heat=rec["heat"], cls=rec["cls"], pilots=[rec["cells"].get(s, "") for s in rec["order"]])
        f = self.path(rec)
        try:
            os.makedirs(os.path.dirname(f), exist_ok=True)
            open(f + ".pending", "w").close()        # before the network: a failure of any kind leaves it to be tried again
            self.post(f"heats/{rec['id']}", gzip.compress(json.dumps(rec, separators=(",", ":"), ensure_ascii=False).encode()),
                      {"x-heat-entry": json.dumps(entry, ensure_ascii=True)})   # ASCII: header values go out as latin-1
        except Exception as e: self.upload_err = f"heat upload {rec['id']}: {why(e)}"; return False
        if self.rec_dir: open(f[:-len(".json")] + ".dvr", "w").close()   # its DVR next, on dvr_loop's thread (before .pending goes: a failed write is tried again)
        os.remove(f + ".pending")
        if not glob.glob(f"{self.out}/*/*.json.pending"): self.upload_err = None   # another heat may still be failing
        return True

    # --- the heat's DVR (night_heats.py's cut, from the recording still being written): the clips, then race.json, which
    # turns the replay page to them. Minutes of ffmpeg and upload, so on its own thread; a failure is tried again every 30 s,
    # DVR_TRIES times. A clip over the Worker's 100 MB request limit goes without its video (that pilot keeps the line only)
    def dvr(self, marker, now):
        from night_heats import cut_heat, recordings
        rec = json.load(open(marker[:-len(".dvr")] + ".json")); hid = rec["id"]
        base = f"{os.path.dirname(marker)}/dvr"; d = f"{base}/{hid}"
        if not os.path.exists(f"{d}/race.json"):     # cut once: a failed upload sends the same files again
            h = dict(id=hid, start=rec["start"], end=rec["end"], heat=rec["heat"], cls=rec["cls"], cells=rec["cells"], order=rec["order"])
            d = cut_heat(h, recordings(self.rec_dir, day(rec["start"] / 1000), now=now), base, relay=self.out, nvenc=self.nvenc, log=lambda m: print("dvr:", m, flush=True))
            if not d: os.replace(marker, marker + ".none"); return   # no recording or no answers: the replay stays the live one
        race = json.load(open(f"{d}/race.json"))
        for p in race["pilots"]:
            f = f"{d}/{p['stream']}.mp4"
            if os.path.getsize(f) > DVR_MAX:
                self.dvr_note = f"dvr {hid} {p['stream']}: {os.path.getsize(f) >> 20} MB, sent without its video"; print(self.dvr_note, flush=True); p.pop("video", None); continue
            with open(f, "rb") as o: self.post(f"heats/{hid}/{p['stream']}.mp4", o.read(), {"content-type": "video/mp4"})
        self.post(f"heats/{hid}/race.json", json.dumps(race, separators=(",", ":"), ensure_ascii=False).encode(), {})
        os.remove(marker); self.dvr_tried.pop(marker, None); print(f"dvr: {hid} up", flush=True)

    def dvr_loop(self):
        while True:
            try:
                for m in sorted(glob.glob(f"{self.out}/*/*.dvr")):
                    n, last = self.dvr_tried.get(m, (0, 0.0)); now = time.time()   # each heat its own now: the one before may take minutes
                    if now - last < RETRY_EVERY: continue
                    try: self.dvr(m, now)
                    except Exception as e:
                        self.dvr_tried[m] = (n + 1, now); self.dvr_fail = f"dvr {os.path.basename(m)}: {why(e)}"
                        if n + 1 >= DVR_TRIES: os.replace(m, m + ".failed"); self.dvr_tried.pop(m, None)
                if not self.dvr_tried: self.dvr_fail = None   # nothing failing now (a heat given up shows in status: dvr_failed)
                self.dvr_err = self.dvr_fail or self.dvr_note
            except Exception as e: self.dvr_err = f"dvr loop: {why(e)}"
            time.sleep(5)

    def retry_pending(self, now):
        self.last_retry = now
        for p in sorted(glob.glob(f"{self.out}/*/*.json.pending")):
            try: rec = json.load(open(p[:-len(".pending")]))
            except Exception as e:                   # its record never reached the disk: nothing to send, ever
                self.bg_err = f"pending {os.path.basename(p)}: {why(e)} (kept as .lost)"; os.replace(p, p[:-len(".pending")] + ".lost"); continue
            self.upload(rec)

    # --- threads
    def line(self, raw, now):                        # one SSE line; a bad row is skipped and reported, the stream goes on
        if not raw.startswith(b"data: "): return
        try: self.on_row(json.loads(raw[6:]), now)
        except Exception as e: self.bg_err = f"read: {why(e)}"

    def read(self):
        while True:
            try:
                for raw in urllib.request.urlopen(self.push_url, timeout=86400): self.line(raw, time.time())
            except Exception as e:                   # the tracker is not running, or went away: its next start is read again
                if not isinstance(e, OSError): self.bg_err = f"read: {why(e)}"
                time.sleep(1)

    def loop(self):
        next_tick = 0.0
        while True:
            now = time.time()
            try: self.flush(now)
            except Exception as e: self.bg_err = f"flush: {why(e)}"
            if now >= next_tick:
                next_tick = now + 1
                try: self.tick(now)
                except Exception as e: self.bg_err = f"tick: {why(e)}"
            time.sleep(0.1)

    def slow(self):
        try: self.recover(time.time())
        except Exception as e: self.bg_err = f"recover: {why(e)}"
        while True:
            try: self.work(time.time())
            except Exception as e: self.bg_err = f"work: {why(e)}"
            time.sleep(1)

    def start_threads(self):
        for f in (self.read, self.loop, self.slow) + ((self.dvr_loop,) if self.rec_dir else ()): threading.Thread(target=f, daemon=True).start()
        return self


def why(e):                                          # the Worker's reason ("bad heat id", ...) rides in the body of an HTTP error
    body = ""
    if isinstance(e, urllib.error.HTTPError):
        try: body = " " + e.read(200).decode(errors="replace")
        except Exception: pass
    return f"{e}{body}"


def read_key(path="~/.config/ghostline/key"):
    with open(os.path.expanduser(path)) as f: return f.read().strip()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--push", default="http://127.0.0.1:8765/"); ap.add_argument("--base", required=True)
    ap.add_argument("--event", required=True); ap.add_argument("--pilots"); ap.add_argument("--out", default="live/relay"); ap.add_argument("--key", default="~/.config/ghostline/key")
    a = ap.parse_args()
    get = (lambda: json.loads(urllib.request.urlopen(a.pilots, timeout=1).read(65536))) if a.pilots else None
    r = Relay(a.push, a.base, a.event, read_key(a.key), pilots=get, out_dir=a.out).start_threads()
    print(f"relay: {a.push} -> {a.base}/{a.event}/", flush=True)
    while True:
        time.sleep(10); st = r.status(); print(f"relay: sent {st['age']:.1f} s ago" if st["age"] is not None else "relay: nothing sent yet", f"pending {st['pending']}" if st["pending"] else "", ("- " + st["err"]) if st["err"] else "", flush=True)
