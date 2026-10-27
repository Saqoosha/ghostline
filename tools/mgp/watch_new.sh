#!/bin/bash
# Poll the pilot-review manifest and download every recording of a given day as it appears (run on the studio
# from ~/mgp: raw/, urls.txt and aliases.json are extended; new.log lists what arrived).
# usage: watch_new.sh <YYYY-MM-DD> [interval s=600]
DAY="$1"; INT="${2:-600}"
M=https://race-dvr-public.s3.us-east-1.amazonaws.com/pilot-review
while true; do
  curl -sS "$M/manifest.json" -o manifest.json || { sleep "$INT"; continue; }
  python3 - "$DAY" <<'EOF'
import json, sys, os, re, subprocess
day = sys.argv[1]
r = json.load(open("manifest.json", encoding="utf-8-sig")); r = r if isinstance(r, list) else r["races"]
have = set(l.rstrip("\n").split("\t")[1] for l in open("urls.txt") if "\t" in l) if os.path.exists("urls.txt") else set()
al = json.load(open("aliases.json")) if os.path.exists("aliases.json") else {}
for x in sorted(r, key=lambda x: x["name"]):
    n = x["name"].replace(" ", "_")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", n): print("skipped, not a plain file name:", repr(n), flush=True); continue   # it goes into shell commands and paths
    # an archived recording (moved under Archive/<day>/ the next day) is still one to fetch: its key says where it is now
    if not x["name"].startswith(day) or x["size"] < 20e6 or n in have: continue
    url = ("https://race-dvr-public.s3.us-east-1.amazonaws.com/" + x["key"].replace(" ", "%20")) if x.get("key") else \
          "https://race-dvr-public.s3.us-east-1.amazonaws.com/pilot-review/" + x["url"]
    if subprocess.run(["curl", "-sS", "--retry", "3", "-o", f"raw/{n}.part", url]).returncode == 0:
        os.rename(f"raw/{n}.part", f"raw/{n}")
        open("urls.txt", "a").write(f"{url}\t{n}\n"); al[n] = x.get("alias", "").replace("Â", ""); json.dump(al, open("aliases.json", "w"), indent=0)
        open("new.log", "a").write(f"{n}\t{al[n]}\t{x['size']}\n"); print("got", n, al[n], flush=True)
EOF
  sleep "$INT"
done
