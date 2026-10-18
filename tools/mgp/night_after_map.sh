#!/bin/bash
# After the evening map (night_sfm_hloc.py, unit night-hl-inc3): check it, register the D final's daytime frames (the
# anchors) and every frame of the finals, fit the map onto the day map, train the night 3DGS and turn it into the
# viewer's frame. rt4090, /mnt/data/mgpwork/night; log to sk/after_map.log. Uses the finished model, else the latest
# snapshot (when the mapper was stopped early).
cd /mnt/data/mgpwork/night
PY=~/mastenv/bin/python
log() { echo "$(date +%T) $*"; }
while systemctl is-active -q night-hl-inc3; do sleep 60; done
M=sk/sfm/models_srf2                 # a run continued from a snapshot (INPUT) writes the model here, not in 0/
[ -f $M/images.bin ] || M=sk/sfm/models_srf2/0
[ -f $M/images.bin ] || M=$(ls -dt sk/sfm/models_srf2_snap/*/ | head -1)
log "model $M: $($PY night_model_check.py $M 2>&1 | sed -n '1p;$p' | tr '\n' ' ')"
# anchors (the D final at noon: the day localizer's poses there are good, 53-65 inliers) and the finals, side by side
QCAM_FROM=dayloc/rounds/fd_loc NRET=20 $PY -u night_localize.py sk/sfm $M sk/images sk/masks sk/loc_fd fd_s0:3 fd_s1:3 fd_s2:3 fd_s3:3 > sk/loc_fd.log 2>&1 &
WIN=0.7 NRET=5 $PY -u night_localize.py sk/sfm $M sk/images sk/masks sk/loc2 fb_s0 fb_s1 fb_s2 fb_s3 fa_s1 fa_s2 fa_s3 fe_s0 fe_s1 fe_s2 fe_s3 > sk/loc2.log 2>&1 &
wait
log "D final: $(tail -1 sk/loc_fd.log)"
log "finals: $(tail -1 sk/loc2.log)"
cat sk/loc2/results.txt sk/loc_fd/results.txt > sk/loc2/results_all.txt
$PY -c "import json; a = json.load(open('sk/loc2/inliers.json')); a.update(json.load(open('sk/loc_fd/inliers.json'))); json.dump(a, open('sk/loc2/inliers_all.json', 'w'))"
FRAME=../top_frame.json FIT=../fit01.png.json COURSE='dayposes/*/*.json' ANCHOR_PREFIX=fd_ $PY night_anchor.py sk/loc2/results_all.txt sk/loc2/inliers_all.json sk/anchor2 \
  dayloc/rounds/fd_loc dayloc/rounds/fb_loc dayloc/rounds/fa_loc dayloc/rounds/fe_loc -- fb_s0 fb_s1 fb_s2 fb_s3 fa_s1 fa_s2 fa_s3 fe_s0 fe_s1 fe_s2 fe_s3 2>&1 | grep -v "^  " | while read -r l; do log "anchor: $l"; done
[ -f sk/anchor2/transform.json ] || { log "no transform, stopped"; exit 1; }
# night 3DGS from the night feeds, on the day map's frame
rm -rf ~/mgp/gsnight2
FRAME=../top_frame.json FIT=../fit01.png.json COURSE='dayposes/*/*.json' $PY night_gs_dataset.py $M sk/anchor2/transform.json sk/images sk/masks720 ~/mgp/gsnight2 e2100 na11 fa fe 2>&1 | while read -r l; do log "dataset: $l"; done
~/spirula/spirula train in-the-wild --data ~/mgp/gsnight2 --disable-viewer 1 --output-dir-prefix /mnt/data/mgpwork/night/gs \
  --output-dir-name t3 --distraction-robustness strong --floater-suppression strong > gs/t3.log 2>&1
log "3DGS: $(grep -a 'Training complete' gs/t3.log)"
cd .. && $PY to_metric.py night/gs/t3/step-000030000.ckpt/splat.ply top_frame.json fit01.png.json night/gs/t3_metric.ply && \
  $PY ply_to_web.py night/gs/t3_metric.ply night/gs/t3_web.ply && log "web ply: night/gs/t3_web.ply"
log done
