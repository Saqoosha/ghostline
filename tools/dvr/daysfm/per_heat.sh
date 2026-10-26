#!/bin/bash
# as run on rt4090 for JDL R7 10/25: work dir ~/rt/night1025/mgp, clips in ../out (../out_yt), relay copy in ../relay (docs/night-rebuild.ja.md)
# plan.txt (heat, window t0 dur, digital cells, flagged digital cells), afternoon first: register the window of the digital
# cells into the Pro map, localize the flagged ones at 15 fps over the window, fuse with ALIGN (the Pro map's)
set -uo pipefail; cd ~/rt/night1025/mgp; PY=$HOME/mgp/env/bin/python; MPY=$HOME/mastenv/bin/python; log() { echo "[$(date +%T)] $*"; }
BASE=pf2/sparse/1; export ALIGN=/home/saqoosha/rt/night1025/mgp/align_pf2.json   # the small first map: registering onto the 3,644-image one took over 40 min a heat
while read -u 3 h t0 dur dig tgt; do   # fd 3: ffmpeg in the loop reads stdin and ate the rest of the plan
  [ -n "$h" ] || continue; out=pf2/models/$h
  if [ ! -f $out/images.bin ]; then
    rm -rf pf2/images/${h}_* pf2/masks/${h}_*; nice -n 5 $MPY jdl_extract.py $h $t0 $dur 3 pf2 ${dig//,/ } > /dev/null
    rm -rf $out; mkdir -p $out; log "register $h ($(ls pf2/images | grep -c "^${h}_") pilots, $(ls pf2/images/${h}_* | wc -l) files)"
    nice -n 5 $PY register2.py pf2 /mnt/data/mgp/vocab256k.bin $BASE $out $(cd pf2/images && ls -d ${h}_*) 2>&1 | grep -E "registered|pairs|Error|Traceback"
  fi
  [ -f $out/images.bin ] || { log "no model for $h"; continue; }
  rm -rf loc15all/images/${h}_* loc15all/masks/${h}_*; nice -n 5 $MPY jdl_extract.py $h $t0 $dur 15 loc15all $tgt > /dev/null
  m=maps/$h; rm -rf $m; mkdir -p $m/sparse; ln -sfn ../../pf2/database.db $m/database.db; ln -sfn ../../../$out $m/sparse/0
  nice -n 5 $PY ~/mgp/localize.py $m loc_all $(for c in $tgt; do echo loc15all/images/${h}_$c; done) -j 4 2>&1 | grep -E " ok, |Error|Traceback"
  nice -n 5 $PY mgp_fuse.py $out loc_all ../relay fused_all $h 2>&1 | grep -A2 "^$h"
  log "done $h"
done 3< <(sort -r plan.txt)
log RUN2-DONE
