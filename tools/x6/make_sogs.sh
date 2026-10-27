#!/bin/bash
# Mac (node): the 10M scene -> the .sog files that are only shown, never matched: 3M SH3 (the audience pages on a computer)
# and 800k SH1 (phones, the venue's 3D page and its NDI/SRT render). The tracker keeps the 10M .ply.
#   tools/x6/make_sogs.sh <in.ply> <out dir> <name>   -> <out>/<name>.sog (3M) and <out>/<name>-lite.sog (800k)
set -euo pipefail
IN=$1; OUT=$2; NAME=$3; ST=(npx -y @playcanvas/splat-transform@3.10.2 --no-tty -w)
mkdir -p "$OUT"; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
"${ST[@]}" "$IN" --decimate-adaptive 3000000 "$T/3m.ply"            # decimate writes only .ply
"${ST[@]}" "$T/3m.ply" "$OUT/$NAME.sog"
"${ST[@]}" "$T/3m.ply" -H 1 --decimate-adaptive 800000 "$T/lite.ply"
"${ST[@]}" "$T/lite.ply" "$OUT/$NAME-lite.sog"
ls -l "$OUT"
