#!/bin/bash
# rt_power_run.sh (from rt_power.ps1) <tag> <mode: s1|s4|idle> [KEY=VAL ...] - the tracker with every output line stamped with epoch seconds
export PACE=${PACE:-1}                           # a recording is walked at its own rate: without it the GPU is kept full and the power reads high
cd /mnt/c/Users/saqoosha/VDGS/dvr/rt
tag=$1; mode=$2; shift 2
SC=/mnt/c/Users/saqoosha/VDGS/scenes/FDF-2026-R6b-spirula-web-dvr2.ply
D05="map_fdf-r6b-d05.npz,../fdf-r6b-d05/dvr_pinhole.mp4,pw/o_${tag}_d05,truth-d05.json"
S4="$D05 map_race-sf-knt.npz,../race-sf-knt/dvr_pinhole.mp4,pw/o_${tag}_knt,race-sf-knt.json map_race-sf-saqoosha.npz,../race-sf-saqoosha/dvr_pinhole.mp4,pw/o_${tag}_saq,race-sf-saqoosha.json map_race-sf-sena.npz,../race-sf-sena/dvr_pinhole.mp4,pw/o_${tag}_sena,race-sf-sena.json"
case $mode in s1) S=$D05;; s4) S=$S4;; idle) echo "$(date +%s.%N) tracking"; sleep 30; echo "$(date +%s.%N) PIPE idle"; exit;; esac
env GLUE=eng/glue_mix.engine XFEAT=eng/xfeat_fp32.engine SCENE=$SC RCLIP=2 "$@" ~/mastenv/bin/python -u rt_track.py $S 2>&1 \
  | while IFS= read -r l; do printf '%s %s\n' "$(date +%s.%N)" "$l"; done
