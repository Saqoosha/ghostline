#!/bin/bash
# Grow a clean map one race at a time, in time order: each race's pilot folders are registered into the previous
# model with its frames fixed (register.py), so a race only joins where it agrees geometrically with what is there.
# usage: chain.sh <work_dir> <vocab.bin> <first_model> <race>...   (models go to <work_dir>/models/<race>)
set -e
W="$1"; V="$2"; prev="$3"; shift 3
PY="${PY:-$HOME/mgp/env/bin/python}"; HERE="$(cd "$(dirname "$0")" && pwd)"
for r in "$@"; do
  out="$W/models/$r"
  if [ -f "$out/images.bin" ]; then prev="$out"; continue; fi     # the mapper writes straight into out when it continues a model
  folders=$(cd "$W/images" && ls -d ${r}_s*)
  echo "== $r: $(echo $folders | wc -w) folders, from $prev"
  nice -n 10 "$PY" "$HERE/register.py" "$W" "$V" "$prev" "$out" $folders 2>&1 | grep -E "^\["
  prev="$out"
done
