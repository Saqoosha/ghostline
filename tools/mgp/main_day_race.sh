#!/bin/bash
# a daytime Main race on rt4090: 4-up feeds at 15 fps, localized on mapfinal, still frames out -> rounds/<id>_still
set -e
cd /mnt/data/mgp_studio; id=$1; F=raw/$2; PY=/home/saqoosha/mgp/env/bin/python
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 $F)
[ -d rounds/$id/images ] || RACE=$id LAYOUT=4up nice -n 10 $PY extract.py $F rounds/$id 0 $DUR 15 > rounds/$id.extract.log 2>&1
[ -s rounds/${id}_loc/${id}_s0.json ] || [ -s rounds/${id}_loc/${id}_s1.json ] || nice -n 10 $PY localize.py mapfinal rounds/${id}_loc $(ls -d rounds/$id/images/${id}_s*) -j 8 2>&1 | grep -E " ok, |Error|Traceback" > rounds/$id.loc.log
FEEDS=$(ls rounds/${id}_loc | grep "^${id}_s.*json$" | sed s/.json// | tr "\n" " ")
/home/saqoosha/mastenv/bin/python /mnt/data/mgpwork/drop_static.py rounds/${id}_loc rounds/$id/images rounds/$id/masks rounds/${id}_still $FEEDS
echo "$id done: $(cat rounds/$id.loc.log | cut -c1-80 | tr "\n" " ")"
