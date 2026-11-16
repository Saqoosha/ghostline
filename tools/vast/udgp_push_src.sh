#!/usr/bin/env bash
# Run on Studio: the original ZowieBox files a Vast run needs, N at a time (one rsync stream to the US is ~10 MB/s), each
# marked src/<file>.ok on the instance once whole, so udgp_queue.py starts its feeds as soon as it lands.
#   udgp_push_src.sh <port> <user@host> <sources.txt> [N=6]       (run from ~/work/udgp-2026-r3, originals in EventVRX/)
set -euo pipefail
P=$1 H=$2 LIST=$3 N=${4:-6}
ssh -p "$P" "$H" 'mkdir -p /root/udgp/src'
push() { rsync -a --partial -e "ssh -p $P" "EventVRX/$1" "$H:/root/udgp/src/" && ssh -p "$P" "$H" "touch '/root/udgp/src/$1.ok'" && echo "sent $1"; }
export -f push; export P H
S=$(date +%s)
xargs -P "$N" -I{} bash -c 'push "$1"' _ {} < "$LIST"
echo "ALL SENT in $(( $(date +%s) - S )) s"
