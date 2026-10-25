"""rt_mylaps.py: the venue's MYLAPS / AMB lap timing decoder, read for the 3D page's lap timeline (viewer/live.html).
Listens to the decoder's P3 stream (TCP 5403) and sends it nothing: the race's own timing software reads the same decoder.
Keeps the last 15 minutes of passings and serves them, with this box's clock, to any page (CORS *):
  GET http://<box>:8766/passings  ->  {"now": ms, "passings": [{"n": passing number, "tx": transponder, "t": ms, "hits", "strength"}]}
  t is the passing on this box's clock: the decoder's RTC (JDL R7 2026-10-24: 1772.3 s ahead) less its offset, taken as the
  largest (rtc - arrival) of the last 200 records - a record only ever arrives late, so the largest is the least delayed.
Every record also goes to the log, one line each (env LOG, default live/mylaps/<YYYYMMDD>.jsonl).
P3: 0x8E, version, length u16, crc u16, flags u16, type u16, then (field u8, length u8, value) up to 0x8F; 0x8D escapes the
next byte (its value + 0x20). Type 0x0001 is a passing: 0x01 its number, 0x03 the transponder, 0x04 the RTC time (us),
0x05 strength, 0x06 hits; 0x0002 is the decoder's status every 5 s.
  python rt_mylaps.py [decoder host, 10.0.0.200] [port, 8766]"""
import collections, datetime, http.server, json, os, socket, struct, sys, threading, time

HOST = sys.argv[1] if len(sys.argv) > 1 else "10.0.0.200"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8766
KEEP = 15 * 60
LOG = os.environ.get("LOG") or f"{os.path.dirname(os.path.abspath(__file__))}/live/mylaps/{datetime.date.today():%Y%m%d}.jsonl"

lock = threading.Lock()
passings = collections.deque()                       # newest last
offs = collections.deque(maxlen=200)                 # rtc - arrival, ms, of recent records
state = dict(connected=False, last=0.0, err=None)


def unescape(b):
    out, i = bytearray(), 0
    while i < len(b):
        if b[i] == 0x8D and i + 1 < len(b): out.append(b[i + 1] - 0x20); i += 2
        else: out.append(b[i]); i += 1
    return bytes(out)


def fields(r):
    f, i = {}, 10
    while i + 2 <= len(r) - 1:
        t, n = r[i], r[i + 1]; f[t] = r[i + 2:i + 2 + n]; i += 2 + n
    return f


def record(rec, rx, log):
    r = unescape(rec)
    if len(r) < 11: return
    tor = struct.unpack_from("<H", r, 8)[0]; f = fields(r); u = lambda k: int.from_bytes(f.get(k, b""), "little")
    if tor != 0x0001:
        log.write(json.dumps(dict(rx=round(rx), type=tor, f={f"{k:#x}": v.hex() for k, v in f.items()})) + "\n"); return
    rtc = u(0x04) / 1000
    p = dict(n=u(0x01), tx=u(0x03), rtc=round(rtc), hits=u(0x06), strength=u(0x05))
    log.write(json.dumps(dict(rx=round(rx), **p)) + "\n")
    with lock:
        offs.append(rtc - rx); off = max(offs)
        passings.append(dict(p, t=round(rtc - off)))
        while passings and passings[0]["t"] < rx - KEEP * 1000: passings.popleft()


def listen():
    while True:
        try:
            s = socket.create_connection((HOST, 5403), timeout=5); s.settimeout(30); buf = b""
            state.update(connected=True, err=None); print(f"mylaps: listening to {HOST}:5403", flush=True)
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a", buffering=1) as log:
                while True:
                    d = s.recv(4096)                 # the decoder's status comes every 5 s: 30 s of nothing is a dead link
                    if not d: raise ConnectionError("closed by the decoder")
                    rx = time.time() * 1000; state["last"] = rx / 1000; buf += d
                    while True:
                        a = buf.find(b"\x8e"); e = buf.find(b"\x8f", a + 1)
                        if a < 0 or e < 0: break
                        record(buf[a:e + 1], rx, log); buf = buf[e + 1:]
        except Exception as e:
            state.update(connected=False, err=str(e)); print(f"mylaps: {HOST}: {e}", flush=True); time.sleep(5)


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] != "/passings": self.send_error(404); return
        with lock: body = dict(now=round(time.time() * 1000), connected=state["connected"], passings=list(passings))
        b = json.dumps(body).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("access-control-allow-origin", "*")
        self.send_header("cache-control", "no-store"); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass


def reload():                                        # a restart keeps the page's last 15 minutes: they come back from the log
    try: lines = open(LOG).readlines()
    except FileNotFoundError: return
    now = time.time() * 1000
    for l in lines:
        try: p = json.loads(l)
        except ValueError: continue                  # a line cut by a power loss
        if "tx" not in p: continue
        offs.append(p["rtc"] - p["rx"])
        if p["rx"] >= now - KEEP * 1000: passings.append(p)
    if offs:
        off = max(offs)
        for p in passings: p["t"] = round(p["rtc"] - off); p.pop("rx", None)
    print(f"mylaps: {len(passings)} passes of the last {KEEP // 60} min from {LOG}", flush=True)


if __name__ == "__main__":
    reload()
    threading.Thread(target=listen, daemon=True).start()
    http.server.ThreadingHTTPServer(("", PORT), H).serve_forever()
