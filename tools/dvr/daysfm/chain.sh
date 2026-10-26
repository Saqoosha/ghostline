#!/bin/bash
# as run on rt4090 for JDL R7 10/25: work dir ~/rt/night1025/mgp, clips in ../out (../out_yt), relay copy in ../relay (docs/night-rebuild.ja.md)
# MGP-style: one map grown heat by heat (nearest 13:40 first) from the chain of the night (Pro + heats 30 27 31 32 25),
# every heat's digital pilots over its flight window; then each flagged pilot localized against the final map; fuse; pick
set -uo pipefail; cd ~/rt/night1025/mgp; PY=$HOME/mgp/env/bin/python; MPY=$HOME/mastenv/bin/python; log() { echo "[$(date +%T)] $*"; }
export ALIGN=/home/saqoosha/rt/night1025/mgp/align_pro.json
until ! systemctl is-active -q mgp-run2; do sleep 30; done
prev=pf2/models/131716-h25
order=$(awk '{print $1}' plan.txt | $MPY -c "
import sys
hs=[l.strip() for l in sys.stdin if l.strip()]; key=lambda h: abs(int(h[:2])*3600+int(h[2:4])*60+int(h[4:6]) - (13*3600+40*60))
print(' '.join(sorted(hs, key=key)))")
for h in $order; do
  out=pf2/chain/$h; mkdir -p pf2/chain
  if [ ! -f $out/images.bin ]; then
    rm -rf $out; mkdir -p $out; log "chain $h onto $prev"
    nice -n 5 $PY register3.py pf2 /mnt/data/mgp/vocab256k.bin $prev $out $(cd pf2/images && ls -d ${h}_*) < /dev/null 2>&1 | grep -E "registered|Error|Traceback"
  fi
  [ -f $out/images.bin ] && prev=$out
done
log "chain done: $prev"; rm -rf bigmap; mkdir -p bigmap/sparse; ln -sfn ../pf2/database.db bigmap/database.db; ln -sfn ../../$prev bigmap/sparse/0
dirs=$(while read -u 3 h t0 dur dig tgt; do for c in $tgt; do echo loc15all/images/${h}_$c; done; done 3< plan.txt)
nice -n 5 $PY ~/mgp/localize.py bigmap loc_big $dirs -j 10 < /dev/null 2>&1 | grep -E " ok, |Error|Traceback"
nice -n 5 $PY mgp_fuse.py bigmap/sparse/0 loc_big ../relay fused_big $(awk '{print $1}' plan.txt) < /dev/null 2>&1 | grep -A2 -E "^[0-9]{6}-h" | grep -E "^[0-9]{6}|live|fused" | head -200
log CHAIN2-DONE
