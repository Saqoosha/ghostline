#!/bin/bash
# Refine every listed flight in turn, each once its tracking has finished (dvr_pipeline.sh leaves `done`).
# usage: refine_all.sh name [name ...]     flights under /mnt/c/Users/saqoosha/VDGS/dvr
R=/mnt/c/Users/saqoosha/VDGS/dvr
for n in "$@"; do
  until [ -f $R/$n/done ] || [ -f $R/$n/failed ]; do sleep 60; done
  [ -f $R/$n/failed ] && { echo "$n: tracking failed, skipped" >> $R/refine_all.log; continue; }
  echo "[$(date +%H:%M:%S)] $n start" >> $R/refine_all.log
  bash /mnt/c/Users/saqoosha/VDGS/tools/refine_flight.sh $R/$n; rc=$?; echo "[$(date +%H:%M:%S)] $n exit $rc" >> $R/refine_all.log
done
echo "[$(date +%H:%M:%S)] all done" >> $R/refine_all.log
