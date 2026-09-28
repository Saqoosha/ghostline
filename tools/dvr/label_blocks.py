"""Serve the block-labelling page for one flight's frames and save the labels.

The frames, their block grid (frames.json) and the detector's proposal per frame (NNNNN.init.json) come from the
preparation step; labels.json holds {frame: [[0|1] * nbx] * nby}, 1 = a dropout block. The page saves on every edit.
usage: label_blocks.py <label dir> [port 8790]   then open http://localhost:<port>/"""
import http.server, json, os, sys, threading

DIR = os.path.abspath(sys.argv[1]); PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8790
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "label_blocks.html")
LABELS = os.path.join(DIR, "labels.json"); lock = threading.Lock()

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=DIR, **k)
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = open(PAGE, "rb").read(); self.send_response(200); self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if self.path == "/labels":
            body = open(LABELS, "rb").read() if os.path.exists(LABELS) else b"{}"
            self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        super().do_GET()
    def do_POST(self):
        if self.path != "/labels" or self.headers.get("content-type") != "application/json": self.send_error(400); return
        data = json.loads(self.rfile.read(int(self.headers["content-length"])))
        with lock:
            cur = json.load(open(LABELS)) if os.path.exists(LABELS) else {}
            cur.update(data); tmp = LABELS + ".tmp"; json.dump(cur, open(tmp, "w")); os.replace(tmp, LABELS)
        self.send_response(204); self.end_headers()
    def log_message(self, *a): pass

print(f"labelling {DIR} at http://localhost:{PORT}/", flush=True)
http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
