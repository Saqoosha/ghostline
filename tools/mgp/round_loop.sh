#!/bin/bash
# The Mac-side loop for a race day: every INT s, queue newly downloaded rounds on the studio (round_queue_studio.sh) (from the studio's new.log),
# finish every round whose localization is done (round_mac.sh), and publish the page with all finished rounds.
# usage: round_loop.sh <publish name> [interval s=600]      (from the repo root; log: build/mgp/round_loop.log)
NAME="$1"; INT="${2:-600}"; ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
DONE=build/mgp/rounds_done.txt; touch "$DONE"
while true; do
  # 1. queue new recordings ("Race N" in the alias) that rt4090 does not have yet, once the round has official laps
  #    (a round without any, e.g. one called off, is not worth localizing)
  python3 -I tools/mgp/livefpv_laps.py data/mgp/livefpv_laps.json > /dev/null 2>&1
  ssh studio 'cat ~/mgp/new.log' 2>/dev/null | while IFS=$'\t' read -r f alias size; do
    # a qualifier is "Bracket · Race N", later renamed "Qualifying · Race N · Qualifying"; the eliminations
    # ("Bracket · Race N · Winner Bracket", "· Redemption", "· Final") restart at Race 1 and are not qualifiers
    n=$(echo "$alias" | sed -nE 's/^(Bracket · Race ([0-9]+)( · Bracket)?|Qualifying · Race ([0-9]+) · Qualifying)$/\2\4/p'); [ -z "$n" ] && continue
    [[ "$f" =~ ^[A-Za-z0-9._-]+$ ]] || continue          # the name comes from the public manifest and goes into a remote shell line
    laps=$(python3 -c "import json;r=[x for x in json.load(open('data/mgp/livefpv_laps.json')) if x['round']==$n];print(sum(len(p['laps']) for x in r for p in x['pilots']))")
    [ "${laps:-0}" -eq 0 ] && continue
    url="https://race-dvr-public.s3.us-east-1.amazonaws.com/pilot-review/races/$(echo "$f" | sed 's/_/%20/')"
    ssh -n studio "grep -q '^$n ' ~/mgp/rounds/queue.txt 2>/dev/null || echo '$n $f' >> ~/mgp/rounds/queue.txt"
  done
  # 2. finish localized rounds that have official laps
  changed=""
  for n in $(ssh -n studio 'cd ~/mgp/rounds && grep -l " ok, " q*.loc.log 2>/dev/null' | sed -E 's/q0*([0-9]+)\.loc\.log/\1/'); do
    grep -qx "$n" "$DONE" && continue
    laps=$(python3 -c "import json,sys;r=[x for x in json.load(open('data/mgp/livefpv_laps.json')) if x['round']==$n];print(sum(len(p['laps']) for x in r for p in x['pilots']))")
    if [ "${laps:-0}" -eq 0 ]; then echo "$(date +%T) round $n: no official laps yet"; continue; fi
    echo "$(date +%T) round $n: finishing"
    if bash tools/mgp/round_mac.sh "$n"; then echo "$n" >> "$DONE"; changed=1; else echo "$(date +%T) round $n: failed"; fi
  done
  # 3. publish every round in rounds.json (a round list would publish a rounds.json of only those rounds)
  if [ -n "$changed" ]; then
    bash tools/mgp/publish_mgp.sh "$NAME" viewer/public/mgp-dev 2>&1 | grep -E "^https|rror"
  fi
  sleep "$INT"
done
