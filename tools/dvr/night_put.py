"""night_put.py: the night rebuild (night_heats.py's <out>/<heat id>/: <cell>.mp4 and race.json) straight from the venue box
to the audience site, through the same Worker route the relay puts each heat's DVR with (PUT /<event>/api/heats/<id>/<file>,
RELAY_KEY): no R2 key on the box and no copy to the Mac (JDL R7 10/25: the field's uplink went down with 1.6 GB on the box).
The clips first, race.json last, as rt_relay.py does: a page never finds a race.json whose clips are not there yet.
  python night_put.py <event> <out> [heat id ...]     env RELAY_BASE (https://ghostline.saqoo.sh), RELAY_KEYFILE, DRY_RUN=1"""
import json, os, sys, urllib.request

DVR_MAX = 95 << 20                                    # the Worker takes 100 MB a request (rt_relay.py DVR_MAX)


def files(heat_dir):                                  # (published name, path) in upload order, or None when it is not a rebuilt heat
    race = os.path.join(heat_dir, "race.json")
    if not os.path.exists(race): return None
    clips = [p["video"] for p in json.load(open(race))["pilots"] if p.get("video")]
    return [(c, os.path.join(heat_dir, c)) for c in clips] + [("race.json", race)]


def main():
    event, out, only = sys.argv[1], sys.argv[2], set(sys.argv[3:])
    base = os.environ.get("RELAY_BASE", "https://ghostline.saqoo.sh").rstrip("/"); dry = os.environ.get("DRY_RUN") == "1"
    key = None if dry else open(os.path.expanduser(os.environ.get("RELAY_KEYFILE", "~/.config/ghostline/key"))).read().strip()
    for hid in sorted(os.listdir(out)):
        fs = None if only and hid not in only else files(os.path.join(out, hid))
        if not fs: continue
        for name, path in fs:
            n = os.path.getsize(path)
            if n > DVR_MAX: print(f"{hid}/{name}: {n >> 20} MB, over the Worker's limit - skipped", flush=True); continue
            if dry: print(f"{hid}/{name} {n >> 10} kB", flush=True); continue
            req = urllib.request.Request(f"{base}/{event}/api/heats/{hid}/{name}", data=open(path, "rb").read(), method="PUT", headers={
                "authorization": f"Bearer {key}", "user-agent": "ghostline-night/1",   # named: Cloudflare refuses urllib's default (403, error 1010)
                "content-type": "video/mp4" if name.endswith(".mp4") else "application/json"})
            with urllib.request.urlopen(req, timeout=300 if name.endswith(".mp4") else 30) as r: r.read()
        print(f"{hid}: {len(fs)} files up", flush=True)


if __name__ == "__main__": main()
