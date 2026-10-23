#!/bin/bash
# Mac, at the venue: a second X6 + RTK walk (2026-10-24, overcast) -> rt4090 -> SfM -> training -> scene and map, unattended.
# The newest .insv on the X6 card, the RTK log that started just before it (beepsync/<UTC>.csv), the same venue origin as
# the 10-23 walk, its own work dir; the scene is JDL-2026-R7-am (~/scenes/JDL-2026-R7-am-s30000.ply, ~/rt/map_JDL-2026-R7-am-s30000.npz).
#   tools/x6/jdl-r7-morning.sh [host: rt4090 | saqoosha@192.168.2.32]   (about 1.5 h of GPU; watch: ssh <host> tail -f <W>/logs_run.txt)
#   COPY_ONLY=1: copy and stop (while the tracker has the GPU); run again without it to process. DRY_RUN=1: only which files
set -euo pipefail
H=${1:-rt4090}; SSH=(ssh -o HostKeyAlias=rt4090); SCP=(scp -q -o HostKeyAlias=rt4090)
HERE=$(cd "$(dirname "$0")" && pwd)
W=/mnt/data/JDL-2026-R7-am; NAME=JDL-2026-R7-am
X=${X6_DIR:-/Volumes/X6/DCIM/Camera01}; R=${RTK_DIR:-"/Volumes/NO NAME/beepsync"}
V=$(ls -t "$X"/VID_*_00_*.insv | head -1); STEM=$(basename "$V" .insv)
# the video's start (JST, in its name) as UTC, then the newest RTK log that began before it
VT=$(echo "$STEM" | sed -E 's/VID_([0-9]{8})_([0-9]{6}).*/\1\2/')
VU=$(TZ=UTC date -j -f "%Y%m%d%H%M%S %z" "$VT +0900" +%Y%m%d_%H%M%S)
LOG=$(ls "$R"/*.csv | xargs -n1 basename | sort | awk -v t="$VU" '$0 < t ".csv"' | tail -1)
echo "walk $STEM (UTC $VU), RTK $LOG, $(du -h "$V" | cut -f1)"
[ -n "$LOG" ] || { echo "no RTK log before the walk" >&2; exit 1; }
[ -z "${DRY_RUN:-}" ] || exit 0                    # DRY_RUN=1: only which files it would take
"${SSH[@]}" "$H" "mkdir -p $W/raw/x6 $W/raw/rtk/beepsync"
"${SCP[@]}" "$R/$LOG" "$R/${LOG%.csv}"*.ubx "$R/${LOG%.csv}"*.rtcm3 "$H:$W/raw/rtk/beepsync/" 2>/dev/null || "${SCP[@]}" "$R/$LOG" "$H:$W/raw/rtk/beepsync/"
[ "$(stat -f %z "$V")" = "$("${SSH[@]}" "$H" "stat -c %s $W/raw/x6/$STEM.insv 2>/dev/null")" ] || "${SCP[@]}" "$V" "$H:$W/raw/x6/"   # already there: the run after COPY_ONLY
[ "$(stat -f %z "$V")" = "$("${SSH[@]}" "$H" "stat -c %s $W/raw/x6/$STEM.insv")" ] || { echo "size mismatch after copy" >&2; exit 1; }
"${SCP[@]}" "$HERE/jdl-r7-sfm.sh" "$HERE/jdl-r7-train.sh" "$H:$W/"
[ -z "${COPY_ONLY:-}" ] || { echo "copied to $H:$W (COPY_ONLY: not started; the GPU stays with the tracker)"; exit 0; }
P=v${STEM##*_}
"${SSH[@]}" "$H" "sudo -n systemd-run --uid=saqoosha --unit=jdl-am --setenv=HOME=/home/saqoosha --working-directory=$W \
  --setenv=W=$W --setenv=NAME=$STEM --setenv=LOG=raw/rtk/beepsync/$LOG --setenv=P=$P \
  bash -c 'bash jdl-r7-sfm.sh > logs_run.txt 2>&1 && NAME=$NAME bash jdl-r7-train.sh >> logs_run.txt 2>&1'; sleep 2; systemctl is-active jdl-am"
echo "started on $H: $W/logs_run.txt"
