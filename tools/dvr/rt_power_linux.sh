#!/bin/bash
# rt_power_linux.sh <tag> <powerlimit W> <s1|s4|idle> [KEY=VAL ...]: native Linux - GPU (nvidia-smi 200 ms) and CPU package (RAPL 1 s) power around a tracker run
export PACE=${PACE:-1}                           # a recording is walked at its own rate: without it the GPU is kept full and the power reads high
tag=$1; pl=$2; mode=$3; shift 3; cd ~/rt; mkdir -p pw; export PATH=$HOME/mastenv/bin:/usr/local/cuda-12.9/bin:$PATH
sudo nvidia-smi -pl $pl >/dev/null; sleep 3
nvidia-smi --query-gpu=timestamp,power.draw,clocks.gr,clocks.mem,utilization.gpu,temperature.gpu --format=csv,noheader,nounits -lms 200 -f pw/$tag.gpu.csv & G=$!
( R=/sys/class/powercap/intel-rapl:0/energy_uj; M=$(sudo cat /sys/class/powercap/intel-rapl:0/max_energy_range_uj); p=$(sudo cat $R); pt=$(date +%s%N)
  while true; do sleep 1; e=$(sudo cat $R); t=$(date +%s%N); d=$((e - p)); [ $d -lt 0 ] && d=$((d + M)); echo "$((t / 1000000)),$((d * 1000000 / (t - pt)))" ; p=$e; pt=$t; done ) > pw/$tag.cpu.csv & C=$!
sleep 2
SC=~/scenes/FDF-2026-R6b-spirula-web-dvr2.ply
D05="map_fdf-r6b-d05.npz,../fdf-r6b-d05/dvr_pinhole.mp4,pw/o_${tag}_d05,truth-d05.json"
S4="$D05 map_race-sf-knt.npz,../race-sf-knt/dvr_pinhole.mp4,pw/o_${tag}_knt,race-sf-knt.json map_race-sf-saqoosha.npz,../race-sf-saqoosha/dvr_pinhole.mp4,pw/o_${tag}_saq,race-sf-saqoosha.json map_race-sf-sena.npz,../race-sf-sena/dvr_pinhole.mp4,pw/o_${tag}_sena,race-sf-sena.json"
case $mode in s1) S=$D05;; s4) S=$S4;; idle) S=;; esac
if [ -z "$S" ]; then echo "$(date +%s.%N) tracking" > pw/$tag.log; sleep 30; echo "$(date +%s.%N) PIPE idle" >> pw/$tag.log
else env GLUE=eng_linux/glue_mix.engine XFEAT=eng_linux/xfeat_fp32.engine SCENE=$SC RCLIP=2 "$@" ~/mastenv/bin/python -u rt_track.py $S 2>&1 \
  | while IFS= read -r l; do printf '%s %s\n' "$(date +%s.%N)" "$l"; done > pw/$tag.log; fi
sleep 2; kill $G $C; sudo nvidia-smi -pl 450 >/dev/null; echo "done $tag"
