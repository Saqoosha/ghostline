#!/bin/bash
# a night Main race: 4-up feeds and full-size copies, then night_race.sh and the still-frame filter -> night/race/<id>/still
set -e
cd /mnt/data/mgp_studio; id=$1; F=raw/$2; PY=/home/saqoosha/mgp/env/bin/python
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 $F)
[ -d rounds/$id/images ] || RACE=$id LAYOUT=4up nice -n 10 $PY extract.py $F rounds/$id 0 $DUR 15 > rounds/$id.extract.log 2>&1
[ -d rounds/$id/full ] || nice -n 10 $PY fullres_frames.py $F rounds/$id rounds/$id/full 15 >> rounds/$id.extract.log 2>&1
mkdir -p /mnt/data/mgpwork/night/src/rounds/$id
ln -sfn /mnt/data/mgp_studio/rounds/$id/full /mnt/data/mgpwork/night/src/rounds/$id/full
ln -sfn /mnt/data/mgp_studio/rounds/$id/masks /mnt/data/mgpwork/night/src/rounds/$id/masks
cd /mnt/data/mgpwork/night
[ -d race/$id/gap ] || ./night_race.sh $id ${3:-gs/t45.ply} > race/$id.log 2>&1
FEEDS=$(ls race/$id/gap | sed s/.json// | tr "\n" " ")
FRAME=../top_frame.json FIT=../fit01.png.json /home/saqoosha/mastenv/bin/python ../drop_static.py race/$id/gap sk/images sk/masks720 race/$id/still $FEEDS
echo "$id done"
