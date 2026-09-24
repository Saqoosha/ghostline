#!/bin/bash
# CPR on every frame with a start pose (5,564 of hdz_0067's 5,676), on win4090 WSL as a scheduled task: ~2 h.
# Round 1 (yaw 0, +-25 from run 10) -> fuse -> round 2 (1 view from the fused track) -> fuse -> round 3 (only the
# frames round 2 left to the fill, rendered from the fused track: at #3701-#3716 the fill raised the inliers from
# 0-55 to 97-193, so a render there matches again) -> fuse. The rounds fuse with ROT_SIGMA=0, as measured; the final
# track is written smoothed (poses60_cpr2.json) and unsmoothed (_raw).
# Needs, in the working dir: the cpr_*.py scripts, dvr_pinhole.mp4(.json), poses60_refined10.json, and
# frames_all.txt (the frame indices of poses60_refined10.json from take-off on, space separated).
set -euo pipefail
export CUDA_HOME=/usr/local/cuda-12.9 PATH=/usr/local/cuda-12.9/bin:$PATH
cd /mnt/c/Users/saqoosha/JDL-2026-R6/dvr
rm -f cprall.done
PLY=/mnt/c/Users/saqoosha/JDL-2026-R6-fix/out/JDL-2026-R6-fix-web.ply
PY=~/mastenv/bin/python
round() {   # $1 name, $2 start poses, $3 yaws, $4 frame list
  # resumes: kept renders and jsonl lines are reused, so after changing the inputs remove ~/$1 and $1.jsonl first
  mkdir -p ~/$1; rm -f ~/$1/DONE   # a DONE left by an earlier run would make the matcher skip unrendered frames
  # DONE also after a crash, so the matcher stops waiting; the render's own status is checked after the match
  ( s=0; YAWS=$3 ~/gsenv/bin/python cpr_render.py $PLY dvr_pinhole.mp4.json $2 $4 ~/$1 > $1_render.log 2>&1 || s=$?; touch ~/$1/DONE; exit $s ) &
  rp=$!
  $PY cpr_match.py dvr_pinhole.mp4 ~/$1 $4 $1.jsonl > $1_match.log 2>&1
  wait $rp
}
round cprall1 poses60_refined10.json 0,-25,25 frames_all.txt
ROT_SIGMA=0 $PY cpr_fuse.py poses60_refined10.json cprall1.jsonl poses_cprall1.json > cprall1_fuse.log 2>&1
round cprall2 poses_cprall1.json 0 frames_all.txt
ROT_SIGMA=0 $PY cpr_fuse.py poses60_refined10.json cprall2.jsonl poses_cprall2.json > cprall2_fuse.log 2>&1
$PY -c "import json; print(' '.join(str(p['i']) for p in json.load(open('poses_cprall2.json'))['poses'] if p and p['src'] == 'cpr-fill'))" > frames_fill.txt
rm -rf ~/cprall3 cprall3.jsonl                    # its frames and poses come from round 2, so never resume it
round cprall3 poses_cprall2.json 0 frames_fill.txt
# a fill frame takes its round-3 record when that one reaches MIN_INL: its round-2 record is the one the gate dropped
$PY -c "
import json
m = {r['i']: r for r in map(json.loads, open('cprall2.jsonl'))}
fill = set(map(int, open('frames_fill.txt').read().split()))
for r in map(json.loads, open('cprall3.jsonl')):
    if r['i'] in fill and r.get('inliers', 0) >= 100: m[r['i']] = r
open('cprall23.jsonl', 'w').write(''.join(json.dumps(m[i]) + '\n' for i in sorted(m)))"
ROT_SIGMA=0 $PY cpr_fuse.py poses60_refined10.json cprall23.jsonl poses60_cpr2_raw.json > cprall3_fuse.log 2>&1
$PY cpr_fuse.py poses60_refined10.json cprall23.jsonl poses60_cpr2.json >> cprall3_fuse.log 2>&1
# the matches as they are, for comparison: no gate, fusion or fill (a frame without >= 100 inliers stays empty)
$PY -c "
import json
S = json.load(open('poses60_cpr2.json')); out = [None] * len(S['poses'])
for r in map(json.loads, open('cprall23.jsonl')):
    if r.get('inliers', 0) >= 100 and r['i'] < len(out):
        out[r['i']] = {'i': r['i'], 't': round(S['t0'] + r['i'] / S['fps'], 4), 'pos': [round(x, 3) for x in r['pos']], 'quat': [round(x, 6) for x in r['quat']], 'src': 'cpr'}
json.dump({**{k: v for k, v in S.items() if k != 'poses'}, 'poses': out}, open('poses60_cpr_match.json', 'w'))"
echo ALL-DONE > cprall.done
