#!/bin/bash
# A big z-up 3DGS PLY -> a PlayCanvas streamed-SOG viewer (lod-meta.json + the SuperSplat viewer page), the way the
# WovenCity-3DGS viewer is built: cut into chunks in file order, turn each Y up, halve it level by level,
# then build all levels into one lod-meta.json and point the viewer page at it.
#   udgp_hq_lod.sh <in.ply> <outdir> [CHUNK=10000000] [LEVELS=5] [JOBS=3]
# Writes <outdir>/parts/c<i>_l<k>.ply and <outdir>/site/{index.html,index.js,index.css,settings.json,lod/}.
# Serve <outdir>/site over HTTP (python3 -m http.server) and open index.html.
set -euo pipefail
IN=$1; OUT=$2; CHUNK=${3:-10000000}; LEVELS=${4:-5}; JOBS=${5:-3}
ST="npx -y @playcanvas/splat-transform@3.11.0"
mkdir -p "$OUT/parts" "$OUT/site/lod"
N=$(head -c 4000 "$IN" | LC_ALL=C grep -a -m1 '^element vertex' | awk '{print $3}')
K=$(( (N + CHUNK - 1) / CHUNK ))
echo "$N splats -> $K chunks, levels 0..$LEVELS"
one() {
  i=$1; b="$OUT/parts/c${i}"
  [ -f "${b}_l${LEVELS}.ply" ] && return 0
  python3 - "$IN" "${b}_raw.ply" "$i" "$CHUNK" <<'P'
import sys
src, dst, i, chunk = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
with open(src, "rb") as f:
    header, nprop, n = [], 0, 0
    while True:
        line = f.readline(); header.append(line); s = line.decode().strip()
        if s.startswith("element vertex"): n = int(s.split()[-1])
        elif s.startswith("property"): nprop += 1
        elif s == "end_header": break
    a, b = i * chunk, min((i + 1) * chunk, n)
    f.seek(f.tell() + a * nprop * 4)
    with open(dst, "wb") as o:
        for line in header:
            o.write(f"element vertex {b - a}\n".encode() if line.startswith(b"element vertex") else line)
        left = (b - a) * nprop * 4
        while left:
            buf = f.read(min(left, 1 << 26))
            if not buf: sys.exit(f"{src}: truncated")
            o.write(buf); left -= len(buf)
P
  $ST -q -w "${b}_raw.ply" -N -r -90,0,0 "${b}_l0.ply"
  rm "${b}_raw.ply"
  for k in $(seq 1 "$LEVELS"); do
    $ST -q -w "${b}_l$((k-1)).ply" -d 50% "${b}_l${k}.tmp.ply"
    mv "${b}_l${k}.tmp.ply" "${b}_l${k}.ply"
  done
  echo "chunk $i done $(date +%H:%M:%S)"
}
export -f one; export IN OUT CHUNK LEVELS ST
seq 0 $((K - 1)) | xargs -P "$JOBS" -I{} bash -c 'set -euo pipefail; one {}'   # a failed chunk fails xargs (exported functions run without -e)

args=()
for k in $(seq 0 "$LEVELS"); do
  for f in "$OUT"/parts/c*_l${k}.ply; do args+=("$f" -l "$k"); done
done
NODE_OPTIONS=--max-old-space-size=48000 $ST -w "${args[@]}" "$OUT/site/lod/lod-meta.json"

# the viewer page: made from the smallest level, its bundled .sog dropped, content pointed at the LOD set.
# stochastic defaults to 'off': SuperSplat Viewer's performance mode breaks when switched back off (WovenCity)
$ST -w "$OUT/parts/c0_l${LEVELS}.ply" --unbundled "$OUT/site/index.html"
rm -f "$OUT/site/index.sog"
sed -i '' 's|"contentUrl":"index.sog"|"contentUrl":"lod/lod-meta.json"|; s|"contentUrl": "index.sog"|"contentUrl": "lod/lod-meta.json"|' "$OUT/site/index.html"
grep -q 'lod/lod-meta.json' "$OUT/site/index.html" || { echo "index.html: contentUrl not replaced"; exit 1; }
perl -0pi -e "s/(\? 'off'\n\s*: true\n\s*): false,/\1: 'off',/" "$OUT/site/index.html"
grep -q ": 'off'," "$OUT/site/index.html" || { echo "index.html: stochastic default not replaced"; exit 1; }
echo "viewer: $OUT/site/index.html"
