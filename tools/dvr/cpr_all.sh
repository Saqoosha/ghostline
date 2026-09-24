#!/bin/bash
# CPR on every frame with a start pose (5,564 of hdz_0067's 5,676), on win4090 WSL as a scheduled task: ~2 h.
# Round 1 (yaw 0, +-25 from run 10) -> fuse -> round 2 (1 view from the fused track) -> fuse. The rounds fuse with
# ROT_SIGMA=0, as measured; the final track is written smoothed (poses60_cpr2.json) and unsmoothed (_raw).
# Needs, in the working dir: the cpr_*.py scripts, dvr_pinhole.mp4(.json), poses60_refined10.json, and
# frames_all.txt (the frame indices of poses60_refined10.json from take-off on, space separated).
set -euo pipefail
export CUDA_HOME=/usr/local/cuda-12.9 PATH=/usr/local/cuda-12.9/bin:$PATH
cd /mnt/c/Users/saqoosha/JDL-2026-R6/dvr
rm -f cprall.done
PLY=/mnt/c/Users/saqoosha/JDL-2026-R6-fix/out/JDL-2026-R6-fix-web.ply
round() {   # $1 name, $2 start poses, $3 yaws
  # resumes: kept renders and jsonl lines are reused, so after changing the inputs remove ~/$1 and $1.jsonl first
  mkdir -p ~/$1; rm -f ~/$1/DONE   # a DONE left by an earlier run would make the matcher skip unrendered frames
  # DONE also after a crash, so the matcher stops waiting; the render's own status is checked after the match
  ( s=0; YAWS=$3 ~/gsenv/bin/python cpr_render.py $PLY dvr_pinhole.mp4.json $2 frames_all.txt ~/$1 > $1_render.log 2>&1 || s=$?; touch ~/$1/DONE; exit $s ) &
  rp=$!
  ~/mastenv/bin/python cpr_match.py dvr_pinhole.mp4 ~/$1 frames_all.txt $1.jsonl > $1_match.log 2>&1
  wait $rp
  ROT_SIGMA=0 ~/mastenv/bin/python cpr_fuse.py poses60_refined10.json $1.jsonl poses_$1.json > $1_fuse.log 2>&1
}
round cprall1 poses60_refined10.json 0,-25,25
round cprall2 poses_cprall1.json 0
cp poses_cprall2.json poses60_cpr2_raw.json
~/mastenv/bin/python cpr_fuse.py poses60_refined10.json cprall2.jsonl poses60_cpr2.json >> cprall2_fuse.log 2>&1
echo ALL-DONE > cprall.done
