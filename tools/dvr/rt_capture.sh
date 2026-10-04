#!/usr/bin/env bash
# The Mac side of a live flight: read the USB capture (the goggles' or a receiver's HDMI), send it to the tracker
# (rt_track.py GRID_GST=1, RTP over SRT) and keep what was sent and what came back, so the flight can be replayed.
#   tools/dvr/rt_capture.sh <rec dir> [srt host:port] [feed url]
# Each run of the sender leaves <rec dir>/<mmdd-HHMMSS>.mkv (the H.264 that went out) and .sse (the tracker's answers,
# its PUSH feed). rt_replay.sh turns a pair into the .mp4 + .json the live page replays (?replay=<name>).
# The SRT sink does not wait on the clock (sync=false): waiting, it let frames pile up for 40-60 ms and sent them in
# bursts (arrival gaps p90 51 ms, 22% over 30 ms; without the wait 22 ms and 4%), and the tracker, which takes only
# the newest frame, then ran at the bursts' rate.
# The sender starts again by itself when the capture drops out (a browser opening the same device did that once);
# touch <rec dir>/stop to end. The tracker numbers frames from the connection, and so does the file, as long as the
# tracker is already listening when the sender starts.
set -u
REC="${1:?rec dir}"; SRT="${2:-100.68.63.104:9000}"; FEED="${3:-http://100.68.63.104:8765}"
mkdir -p "$REC"; rm -f "$REC/stop"
until [ -f "$REC/stop" ]; do
  ts=$(date +%m%d-%H%M%S); echo "== $ts sending"
  curl -s -N --retry 600 --retry-delay 1 --retry-connrefused "$FEED" >> "$REC/$ts.sse" & log=$!   # the tracker may still be loading
  gst-launch-1.0 -q -e avfvideosrc device-index=0 ! video/x-raw,width=1280,height=720,framerate=60/1 ! queue max-size-buffers=2 leaky=downstream \
    ! videoconvert ! x264enc tune=zerolatency speed-preset=ultrafast bitrate=8000 key-int-max=60 ! tee name=t \
    t. ! queue ! rtph264pay config-interval=1 pt=96 mtu=1316 ! srtsink uri="srt://$SRT?mode=caller" latency=80 wait-for-connection=false sync=false \
    t. ! queue ! h264parse ! matroskamux ! filesink location="$REC/$ts.mkv" 2>&1 | grep -E "ERROR|stopped" | head -3
  kill $log 2>/dev/null
  sleep 2
done
