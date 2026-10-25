"""rt_passfwd.py: the lap timing decoder's passes (rt_mylaps.py, :8766/passings) to the audience relay, for watch.html's laps.
Each new pass goes to <RELAY_BASE>/<RELAY_EVENT>/api/push as {k: 'p', t, n, tx, hits}: t is the pass on this box's clock, the
clock the answers' t is on (rt_relay.py), so the page places it on the heat's clock as it does the answers. The Worker keeps
and hands on whatever Msgs come (worker/src/room.ts), so nothing there changes. Apart from rt_control.py on purpose: it
starts and stops without touching the tracker.
  RELAY_BASE=https://ghostline.saqoo.sh RELAY_EVENT=jdl-2026-10-25 python rt_passfwd.py [passings url]"""
import json, os, sys, time, urllib.request

SRC = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766/passings"
URL = f"{os.environ.get('RELAY_BASE', 'https://ghostline.saqoo.sh')}/{os.environ['RELAY_EVENT']}/api/push"
KEY = open(os.path.expanduser(os.environ.get("RELAY_KEYFILE", "~/.config/ghostline/key"))).read().strip()


def main():
    sent, todo = set(), []
    first = True                                     # the passes already there at the start are older than any viewer's backlog: skipped
    while True:
        try:
            ps = json.loads(urllib.request.urlopen(SRC, timeout=2).read())["passings"]
            new = [p for p in ps if p["n"] not in sent]
            sent.update(p["n"] for p in new)
            if not first: todo += [dict(k="p", t=p["t"], n=p["n"], tx=p["tx"], hits=p.get("hits")) for p in new]
            first = False
        except Exception as e: print("passings:", e, flush=True)
        if todo:
            try:
                req = urllib.request.Request(URL, data=json.dumps(dict(sent=int(time.time() * 1000), msgs=todo), separators=(",", ":")).encode(), method="POST",
                                             headers={"authorization": f"Bearer {KEY}", "content-type": "application/json", "user-agent": "ghostline-passfwd/1"})
                with urllib.request.urlopen(req, timeout=3) as f: f.read()
                print(f"sent {len(todo)} passes, last tx {todo[-1]['tx']}", flush=True); todo = []
            except Exception as e: print("push:", e, flush=True); todo = todo[-50:]   # the uplink back: the last few, still within a viewer's backlog
        time.sleep(1)


if __name__ == "__main__": main()
