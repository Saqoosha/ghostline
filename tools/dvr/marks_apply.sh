#!/bin/bash
# local half of a marks round: solve the marks against the current best poses, rebuild the init, list the changed frames.
# usage: marks_apply.sh <N> <prev-init N>   (writes poses60_init<N>.json and changed<N>.txt)
set -e; N=$1; PREV=$2; S=$(cd "$(dirname "$0")" && pwd); D=build/dvr/hdz_0067
cp $D/marks.json $S/marks.backup$N.json
uv run --quiet --with numpy,scipy python3 $S/marks_solve.py $D/marks.json $D/poses60_refined10.json $S/dvr_track_w.json $D/dvr_pinhole.mp4.json $S/dvr_track_manual.json > $S/marks_solve.out 2>&1
grep -c "skipped" $S/marks_solve.out || true; grep "skipped\|solved from\|cannot" $S/marks_solve.out | head -12
uv run --quiet --with numpy,scipy python3 $S/interp60.py $S/dvr_track_manual.json $S/poses60_init${N}_short.json 5876 | tail -1
uv run --quiet --with numpy python3 $S/hairpin_arc.py $S/poses60_init${N}_short.json $D/poses60_init$N.json 1662-1673 > /dev/null
uv run --quiet --with numpy,scipy python3 $S/marks_eval.py $D/marks.json $D/dvr_pinhole.mp4.json $D/poses60_init$N.json
python3 - <<PY
import json, math
A={p['i']:p for p in json.load(open('$D/poses60_init$PREV.json'))['poses'] if p}; B={p['i']:p for p in json.load(open('$D/poses60_init$N.json'))['poses'] if p}
def ang(q1,q2): return 2*math.degrees(math.acos(min(1,abs(sum(a*b for a,b in zip(q1,q2))))))
ch=[i for i in B if i in A and (math.dist(A[i]['pos'],B[i]['pos'])>0.2 or ang(A[i]['quat'],B[i]['quat'])>0.5)]
open('$S/changed$N.txt','w').write(','.join(map(str,sorted(ch)))); print('changed frames:', len(ch))
PY
cp $S/dvr_track_manual.json $D/
