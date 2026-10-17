#!/bin/bash
# Download recordings into ~/mgp/raw on the studio and queue each round once it is complete.
# usage: fetch_queue.sh <list>   (lines "<round> <file> <url>", file names [0-9_-].mp4 only)
R=$HOME/mgp/rounds
while read -r n f url; do
  [[ "$f" =~ ^[0-9_-]+\.mp4$ ]] || { echo "bad name $f"; continue; }
  if [ ! -s "$HOME/mgp/raw/$f" ]; then
    curl -fsS --retry 3 -o "$HOME/mgp/raw/$f.part" "$url" && mv "$HOME/mgp/raw/$f.part" "$HOME/mgp/raw/$f" || { echo "round $n: download failed"; continue; }
  fi
  grep -q "^$n " "$R/queue.txt" || echo "$n $f" >> "$R/queue.txt"
  echo "$(date +%T) round $n queued ($f)"
done < "$1"
