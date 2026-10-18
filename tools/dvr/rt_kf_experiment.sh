#!/bin/bash
# rt4090: does a map grown with the day's real frames bring back the late-afternoon heats on a morning scan?
# 1. the midday heats with KFOUT (the tracking is unchanged, so this is also their baseline) 2. grow the map with those
# frames 3. the late heats twice at once, on two SRT ports: the scan alone, and the grown map with RKF=1.
#   rt_kf_experiment.sh <new rt_track.py> <new rt_map_grow.py> <new rt_replay_day.sh>
set -u; cd ~/rt; export PATH=$HOME/mastenv/bin:/usr/local/cuda-12.9/bin:$PATH
cp rt_track.py rt_track.py.bak-pre-kf; cp "$1" rt_track.py; cp "$2" rt_map_grow.py; cp "$3" rt_replay_day.sh
MAP=map_fdf-2026-10-17-s30000.npz SCENE=$HOME/scenes/FDF-2026-10-17-s30000.ply R=$HOME/rt/rec
mid=$(ls $R/20261017-1{41,42,43,44,45,50,51,52}*.mp4 $R/20261017-1457*.mp4 2>/dev/null)
late=$(for t in 160924 164422 163754 160334 165222 161601 163313 163022; do ls $R/20261017-$t*.mp4; done)
echo "== mid $(echo $mid | wc -w) late $(echo $late | wc -w) $(date +%T)"
KFOUT=$HOME/rt/replay/kf1017 bash rt_replay_day.sh replay/mid-kfout $MAP $SCENE $mid
echo "== grow $(ls replay/kf1017 | wc -l) frames $(date +%T)"
python -u rt_map_grow.py $MAP $SCENE map_fdf-2026-10-17-s30000-grow.npz replay/kf1017 || exit 1
echo "== late x2 $(date +%T)"
PORT=9000 bash rt_replay_day.sh replay/late-base $MAP $SCENE $late &
PORT=9001 RKF=1 bash rt_replay_day.sh replay/late-rkf map_fdf-2026-10-17-s30000-grow.npz $SCENE $late &
wait
echo "== done $(date +%T)"
