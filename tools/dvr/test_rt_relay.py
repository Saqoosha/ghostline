"""tests for rt_relay.py (python -m unittest test_rt_relay -v, from tools/dvr)"""
import datetime, gzip, io, json, os, tempfile, unittest
from unittest import mock
from rt_relay import Heats, Relay

T0 = 1792211516.0                                  # 2026-10-17 13:31:56 JST (the first FDF recording)
JST = datetime.timezone(datetime.timedelta(hours=9))
hms = lambda t: datetime.datetime.fromtimestamp(t, JST).strftime("%H%M%S")


class HeatsTest(unittest.TestCase):
    def test_first_row_starts_heat(self):
        out = Heats().row(T0, "R2")
        self.assertEqual(len(out), 1); h = out[0]
        self.assertEqual(h["k"], "h"); self.assertEqual(h["id"], hms(T0)); self.assertIsNone(h["heat"]); self.assertEqual(h["cells"], {})
        self.assertEqual(h["t"], round(T0 * 1000)); self.assertEqual(h["order"], ["R2", "F1", "R4", "R5"])
        self.assertEqual(Heats().row(T0, "R2")[0]["id"], "133156")

    def test_gap_ends_heat(self):
        hs = Heats(); first = hs.row(T0, "R2")[0]
        for k in range(1, 61): self.assertEqual(hs.row(T0 + k, "F1"), [])
        self.assertEqual(hs.tick(T0 + 89), [])
        self.assertEqual(hs.tick(T0 + 90.1), [{"k": "e", "t": round((T0 + 60) * 1000), "id": first["id"]}])
        self.assertEqual(hs.tick(T0 + 200), [])
        self.assertEqual(hs.row(T0 + 300, "R2")[0]["id"], hms(T0 + 300))   # the next picture starts the next heat

    def test_tracker_restart_keeps_heat(self):
        hs = Heats(); hs.row(T0, "R2")
        self.assertEqual(hs.tick(T0 + 20), [])
        self.assertEqual(hs.row(T0 + 20, "R2"), [])

    def test_names_rename_same_id(self):
        hs = Heats(); first = hs.row(T0, "R2")[0]
        out = hs.names(T0 + 1, {"cells": {"R2": "A"}, "heat": "7", "cls": "Expert"})
        self.assertEqual(len(out), 1); self.assertEqual(out[0]["id"], first["id"]); self.assertEqual(out[0]["heat"], "7")
        self.assertEqual(out[0]["cells"], {"R2": "A"}); self.assertEqual(out[0]["t"], first["t"])
        self.assertEqual(hs.names(T0 + 2, {"cells": {"R2": "A"}, "heat": "7", "cls": "Expert"}), [])

    def test_names_before_heat_go_into_its_id(self):
        hs = Heats(); self.assertEqual(hs.names(T0, {"cells": {"R2": "A"}, "heat": "7 ", "cls": "Expert"}), [])
        h = hs.row(T0 + 1, "R2")[0]
        self.assertEqual(h["id"], hms(T0 + 1) + "-h7"); self.assertEqual(h["cells"], {"R2": "A"})

    def test_heat_number_change_alone_does_not_split(self):
        hs = Heats(); first = hs.row(T0, "R2")[0]; hs.names(T0, {"cells": {"R2": "A"}, "heat": "7", "cls": "Expert"})
        hs.row(T0 + 5, "R2"); out = hs.names(T0 + 6, {"cells": {"R2": "A"}, "heat": "8", "cls": "Expert"})
        self.assertEqual([m["k"] for m in out], ["h"]); self.assertEqual(out[0]["id"], first["id"])

    def test_stale_names_not_given_to_a_new_heat(self):       
        hs = Heats(); hs.names(T0, {"cells": {"R2": "A"}, "heat": "7", "cls": "X"}); hs.row(T0, "R2"); hs.tick(T0 + 40)
        hs.names(T0 + 50, {"cells": {"R2": "A"}, "heat": "7", "cls": "X", "stale": True})
        h = hs.row(T0 + 3600, "R2")[0]
        self.assertEqual(h["id"], hms(T0 + 3600)); self.assertIsNone(h["heat"]); self.assertEqual(h["cells"], {})

    def test_heat_id_ascii_only(self):                            # the Worker's id pattern is ASCII
        hs = Heats(); hs.names(T0, {"cells": {}, "heat": "予選 7", "cls": None})
        self.assertEqual(hs.row(T0, "R2")[0]["id"], hms(T0) + "-h7")

    def test_row_after_the_gap_starts_a_new_heat(self):          # a row 30.4 s after the last, before tick ran, was merged
        hs = Heats(); a = hs.row(T0, "R2")[0]; out = hs.row(T0 + 30.4, "R2")
        self.assertEqual([m["k"] for m in out], ["e", "h"]); self.assertEqual(out[0]["id"], a["id"]); self.assertNotEqual(out[1]["id"], a["id"])

    def test_names_after_the_gap_do_not_rename_the_old_heat(self):
        hs = Heats(); hs.row(T0, "R2")
        out = hs.names(T0 + 40, {"cells": {"R2": "B"}, "heat": "8", "cls": "X"})
        self.assertEqual([m["k"] for m in out], ["e"]); self.assertIsNone(hs.cur)

    def test_null_name_is_empty(self):
        hs = Heats(); hs.names(T0, {"cells": {"R2": None, "F1": "A"}, "heat": None, "cls": None})
        self.assertEqual(hs.row(T0, "R2")[0]["cells"], {"R2": "", "F1": "A"})

    def test_heat_manager_down(self):
        hs = Heats(); hs.row(T0, "R2"); hs.names(T0, {"cells": {"R2": "A"}, "heat": "7", "cls": "Expert"})
        self.assertEqual(hs.names(T0 + 1, {}), [])
        self.assertEqual(hs.names(T0 + 2, {"cells": {"R2": "B"}, "heat": "8", "cls": "Open", "stale": True}), [])
        self.assertEqual(hs.cur["cells"], {"R2": "A"})


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); self.sent = []
        self.r = Relay("http://x/", "http://y", "staging-test", "k", out_dir=self.dir, post=lambda path, body, headers: self.sent.append((path, body, headers)))

    def row(self, now, i, pos=True, **kw):
        r = dict(stream="R2", i=i, done=0, lat=40, fps=30, inl=200, how="track", **kw)
        if pos: r.update(pos=[0.0, 1.0, 2.0], quat=[0, 0, 0, 1])
        else: r["how"] = "none"
        self.r.on_row(r, now)

    def test_post_failure_drops(self):
        def boom(*a): raise OSError("uplink down")
        self.r.post = boom
        for k in range(200): self.row(T0 + k / 30, k)
        self.r.flush(T0 + 10)                          # must not raise
        self.assertEqual(self.r.queue, [])

    def test_revision_applied(self):
        self.row(T0, 10)
        self.row(T0 + 0.3, 11, win=[[10, 1, 2, 3, 0, 0, 0, 1]])
        self.r.flush(T0 + 0.45); self.assertFalse(any(m["k"] == "a" for _, b, _ in self.sent for m in json.loads(b)["msgs"]))
        self.r.flush(T0 + 0.5)
        a = [m for _, b, _ in self.sent for m in json.loads(b)["msgs"] if m["k"] == "a"]
        self.assertEqual(a[0]["p"], [1, 2, 3]); self.assertEqual(a[0]["t"], round((T0 - 0.04) * 1000)); self.assertEqual(a[0]["s"], "R2")

    def test_lost_frames_throttled(self):
        for k in range(30): self.row(T0 + k / 30, k, pos=False)
        self.r.flush(T0 + 1)
        xs = [m for _, b, _ in self.sent for m in json.loads(b)["msgs"] if m["k"] == "x"]
        self.assertEqual(len(xs), 2)

    def test_heat_end_uploads_record(self):
        for k in range(10): self.row(T0 + k / 30, k)
        self.r.flush(T0 + 1); self.r.tick(T0 + 40); self.r.work(T0 + 40)
        put = [s for s in self.sent if s[0].startswith("heats/")]
        self.assertEqual(len(put), 1); path, body, headers = put[0]
        rec = json.loads(gzip.decompress(body)); entry = json.loads(headers["x-heat-entry"])
        self.assertEqual(path, "heats/" + hms(T0)); self.assertEqual(len(rec["answers"]["R2"]), 10); self.assertEqual(entry["id"], hms(T0))
        self.assertEqual(rec["answers"]["R2"][0][:4], [round((T0 - 0.04) * 1000), 0.0, 1.0, 2.0])
        day = os.path.join(self.dir, "20261017")
        self.assertTrue(os.path.exists(os.path.join(day, hms(T0) + ".json")))
        self.assertFalse(any(f.endswith(".pending") for f in os.listdir(day)))
        lines = [json.loads(l) for l in open(os.path.join(self.dir, "heats-20261017.jsonl"))]
        self.assertEqual([l["k"] for l in lines], ["h", "e"])

    def test_failed_upload_stays_pending(self):
        def put_fails(path, body, headers):
            if path.startswith("heats/"): raise OSError("uplink down")
        self.r.post = put_fails
        self.row(T0, 1); self.r.flush(T0 + 1); self.r.tick(T0 + 40); self.r.work(T0 + 40)
        day = os.path.join(self.dir, "20261017")
        self.assertTrue(os.path.exists(os.path.join(day, hms(T0) + ".json.pending")))
        self.r.post = lambda path, body, headers: self.sent.append((path, body, headers))
        self.r.retry_pending(T0 + 80)
        self.assertFalse(os.path.exists(os.path.join(day, hms(T0) + ".json.pending")))
        self.assertTrue(any(s[0].startswith("heats/") for s in self.sent))

    def test_no_heat_start_resent_after_the_last_row(self):       # resends after the last row revived an ended heat
        self.row(T0, 1); self.r.flush(T0 + 0.6); n = len(self.r.queue)
        for k in range(1, 5): self.r.tick(T0 + 10 * k)
        self.assertFalse(any(m["k"] == "h" for m in self.r.queue[n:]))

    def test_end_resent_until_a_post_goes_through(self):        
        self.row(T0, 1); self.r.flush(T0 + 0.6)
        fail = [True]
        def post(path, body, headers):
            if path == "push" and fail[0]: raise OSError("uplink down")
            self.sent.append((path, body, headers))
        self.r.post = post; self.r.tick(T0 + 40); self.r.flush(T0 + 40.1)
        fail[0] = False; self.r.flush(T0 + 41)
        es = [m for p, b, _ in self.sent if p == "push" for m in json.loads(b)["msgs"] if m["k"] == "e"]
        self.assertEqual(len(es), 1)

    def test_tick_does_not_upload(self):                          # the 100 ms loop never waits on a heat upload
        for k in range(10): self.row(T0 + k / 30, k)
        self.r.flush(T0 + 1); self.r.tick(T0 + 40)
        self.assertFalse([s for s in self.sent if s[0].startswith("heats/")])
        self.r.work(T0 + 40)
        self.assertEqual(len([s for s in self.sent if s[0].startswith("heats/")]), 1)

    def test_open_record_closed_at_start(self):                    # a restart mid-heat left it open for good
        self.row(T0, 1); self.r.flush(T0 + 1); self.r.tick(T0 + 11)          # saved at 10 s, still open
        r2 = Relay("http://x/", "http://y", "staging-test", "k", out_dir=self.dir, post=lambda path, body, headers: self.sent.append((path, body, headers)))
        r2.recover(T0 + 100)
        put = [s for s in self.sent if s[0].startswith("heats/")]
        self.assertEqual(len(put), 1); self.assertEqual(json.loads(put[0][2]["x-heat-entry"])["end"], round(T0 * 1000))   # its last answer (T0 - 0.04) is before its start
        self.assertEqual([json.loads(l)["k"] for l in open(os.path.join(self.dir, "heats-20261017.jsonl"))], ["h", "e"])

    def test_keepalive_between_heats(self):                        # the room stays awake, its backlog with it
        self.r.flush(T0); self.r.flush(T0 + 4); self.assertEqual(self.sent, [])
        self.r.flush(T0 + 5.1); self.assertEqual(json.loads(self.sent[0][1])["msgs"], [])

    def test_order_falls_back_when_config_missing(self):           # a fresh box has no config yet
        def order(): raise FileNotFoundError("no ~/.ghostline-control.json")
        r = Relay("http://x/", "http://y", "e", "k", out_dir=self.dir, post=lambda *a: None, order=order)
        r.on_row(dict(stream="R2", i=1, lat=0, how="none"), T0)
        self.assertEqual(r.queue[0]["order"], ["R2", "F1", "R4", "R5"])

    def test_upload_failure_into_a_missing_folder_stays_pending(self):
        def put_fails(path, body, headers):
            if path.startswith("heats/"): raise OSError("uplink down")
        self.r.post = put_fails
        rec = dict(id="133156", start=round(T0 * 1000), end=round(T0 * 1000) + 5000, heat=None, cls=None, cells={}, order=["R2"], answers={})
        self.assertFalse(self.r.upload(rec))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "20261017", "133156.json.pending")))

    def test_bad_row_is_skipped_and_reported(self):
        self.r.line(b'data: {"stream": "R2", "i": 1, "pos": [0, 1], "how": "track"}', T0)
        self.r.line(b'data: not json', T0)
        self.assertIn("read", self.r.status(T0)["err"])
        self.row(T0 + 0.1, 2); self.assertTrue(self.r.held)                 # the next row still goes in

    def test_upload_error_survives_a_good_push(self):
        def put_fails(path, body, headers):
            if path.startswith("heats/"): raise OSError("uplink down")
            self.sent.append((path, body, headers))
        self.r.post = put_fails
        self.row(T0, 1); self.r.flush(T0 + 1); self.r.tick(T0 + 40); self.r.work(T0 + 40); self.r.flush(T0 + 41)
        st = self.r.status(T0 + 41); self.assertEqual(st["pending"], 1); self.assertIn("uplink down", st["err"])

    def test_names_with_kana_go_up_ascii(self):
        self.row(T0, 1); self.r.on_names({"cells": {"R2": "佐藤"}, "heat": "7", "cls": None}, T0 + 1)
        self.r.flush(T0 + 2); self.r.tick(T0 + 40); self.r.work(T0 + 40)
        h = [s[2]["x-heat-entry"] for s in self.sent if s[0].startswith("heats/")][0]
        self.assertTrue(h.isascii()); self.assertEqual(json.loads(h)["pilots"][0], "佐藤")

    def test_recovered_end_not_before_start(self):
        self.row(T0, 1); self.r.flush(T0 + 1); self.r.tick(T0 + 11)
        r2 = Relay("http://x/", "http://y", "staging-test", "k", out_dir=self.dir, post=lambda path, body, headers: self.sent.append((path, body, headers)))
        r2.recover(T0 + 100)
        e = json.loads([s for s in self.sent if s[0].startswith("heats/")][0][2]["x-heat-entry"])
        self.assertGreaterEqual(e["end"], e["start"])

    def test_bad_event_name_refused(self):
        with self.assertRaises(ValueError): Relay("http://x/", "http://y", "FDF 2026", "k", out_dir=self.dir)

    def test_heat_started_after_the_gap_reads_the_order_again(self):
        order = [["A", "B"]]
        r = Relay("http://x/", "http://y", "e", "k", out_dir=self.dir, post=lambda *a: None, order=lambda: order[0])
        r.on_row(dict(stream="A", i=1, lat=0, how="none"), T0); order[0] = ["B", "A"]
        r.on_row(dict(stream="A", i=2, lat=0, how="none"), T0 + 31)
        self.assertEqual([m["order"] for m in r.queue if m["k"] == "h"], [["A", "B"], ["B", "A"]])

    def test_upload_into_an_unwritable_folder_reports(self):
        open(os.path.join(self.dir, "blocked"), "w").close()
        r = Relay("http://x/", "http://y", "e", "k", out_dir=os.path.join(self.dir, "blocked"), post=lambda *a: None)
        rec = dict(id="133156", start=round(T0 * 1000), end=round(T0 * 1000) + 5000, heat=None, cls=None, cells={}, order=["R2"], answers={})
        self.assertFalse(r.upload(rec)); self.assertIn("133156", r.status(T0)["err"])

    def test_pending_without_its_record_stops_retrying(self):
        day = os.path.join(self.dir, "20261017"); os.makedirs(day); open(os.path.join(day, "133156.json.pending"), "w").close()
        self.r.retry_pending(T0)
        self.assertFalse(os.path.exists(os.path.join(day, "133156.json.pending"))); self.assertTrue(os.path.exists(os.path.join(day, "133156.json.lost")))

    def test_upload_waits_for_its_record_on_disk(self):
        self.row(T0, 1); self.r.flush(T0 + 1); self.r.tick(T0 + 40)
        save = self.r.save
        def broken(rec): raise OSError("disk full")
        self.r.save = broken; self.r.work(T0 + 40)
        self.assertFalse([s for s in self.sent if s[0].startswith("heats/")]); self.assertEqual(len(self.r.uploads), 1)
        self.r.save = save; self.r.work(T0 + 41)
        self.assertEqual(len([s for s in self.sent if s[0].startswith("heats/")]), 1); self.assertEqual(self.r.uploads, [])

    def test_requests_name_themselves(self):       # Cloudflare refuses urllib's default user agent (403, error 1010)
        sent = []
        with mock.patch("rt_relay.urllib.request.urlopen", lambda req, timeout: sent.append(req) or io.BytesIO()):
            self.r._post("push", b"{}", {}); self.r._post("heats/133156", b"", {})
        self.assertTrue(all(q.get_header("User-agent", "").startswith("ghostline-relay/") for q in sent))
        self.assertEqual([q.get_method() for q in sent], ["POST", "PUT"])

    def test_order_from_venue_config(self):
        r = Relay("http://x/", "http://y", "e", "k", out_dir=self.dir, post=lambda *a: None, order=lambda: ["A", "B", "C", "D"])
        r.on_row(dict(stream="A", i=1, lat=0, how="none"), T0)
        self.assertEqual(r.queue[0]["order"], ["A", "B", "C", "D"])

    def test_heat_start_carries_when_sent(self):
        self.row(T0, 1); self.r.on_names({"cells": {"R2": "A"}, "heat": "7", "cls": "X"}, T0 + 4)
        hs = [m for m in self.r.queue if m["k"] == "h"]
        self.assertEqual([m["at"] for m in hs], [round(T0 * 1000), round((T0 + 4) * 1000)]); self.assertEqual(hs[1]["t"], round(T0 * 1000))

    def test_status_before_any_send(self):
        self.assertEqual(self.r.status(), dict(ok=False, age=None, event="staging-test", err=None, pending=0))

    def test_status_after_send(self):
        self.row(T0, 1); self.r.flush(T0 + 0.6)
        st = self.r.status(T0 + 1.6); self.assertTrue(st["ok"]); self.assertAlmostEqual(st["age"], 1.0)
        self.assertFalse(self.r.status(T0 + 30)["ok"])           # nothing sent for 30 s: not ok

    def test_heat_start_repeated(self):
        self.row(T0, 1); self.r.flush(T0 + 0.6)
        hs = lambda: [m for _, b, _ in self.sent if not _.get("x-heat-entry") for m in json.loads(b)["msgs"] if m["k"] == "h"]
        n = len(hs()); self.row(T0 + 5, 2); self.r.tick(T0 + 5); self.r.flush(T0 + 5.6); self.assertEqual(len(hs()), n)
        self.row(T0 + 11, 3); self.r.tick(T0 + 11); self.r.flush(T0 + 11.6); self.assertEqual(len(hs()), n + 1)


def fake_push(folder, port=8766, start=0.0, speed=1.0):
    """a stand-in for the tracker's PUSH on a Mac: the rows of <folder>/<cell>.jsonl (a run's answers, e.g. live/archive/...)
    sent as server-sent events at the times they were finished (done), from done = start, each with "stream" and "fps"
    (rt_track.py's PUSH rows). Played once, to the first client; later ones get nothing (a tracker does not start over,
    and rt_relay.py reconnects at once when a stream ends).
      python test_rt_relay.py fake <folder> [port] [start s] [speed]"""
    import glob, http.server, time
    rows = sorted((dict(json.loads(l), stream=os.path.basename(f)[:-6], fps=30) for f in glob.glob(f"{folder}/*.jsonl") for l in open(f)), key=lambda r: r["done"])
    rows = [r for r in rows if r["done"] >= start]
    played = []
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            if played:
                while True: time.sleep(60)
            played.append(1); t0, d0 = time.time(), rows[0]["done"]
            try:
                for r in rows:
                    wait = (r["done"] - d0) / speed - (time.time() - t0)
                    if wait > 0: time.sleep(wait)
                    self.wfile.write(f"data: {json.dumps(r)}\n\n".encode()); self.wfile.flush()
            except OSError: pass
        def log_message(self, *a): pass
    print(f"fake PUSH on :{port}, {len(rows)} rows, {rows[-1]['done'] - rows[0]['done']:.0f} s", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["fake"]: fake_push(sys.argv[2], *(int(sys.argv[3]),) if len(sys.argv) > 3 else (), *(float(v) for v in sys.argv[4:6]))
    else: unittest.main()
