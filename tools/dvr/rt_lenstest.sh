#!/bin/bash
# rt_lenstest.sh <tag> <start s> <seconds> [KEY=VAL ...]: the semifinal grid from this box into four cells, each offered both lens
# definitions - which one does every cell settle on in every heat, and how well does it track?
tag=$1; ss=$2; dur=$3; shift 3; cd ~/rt; export PATH=$HOME/mastenv/bin:/usr/local/cuda-12.9/bin:$PATH; mkdir -p camvar; rm -f camvar/${tag}_*
U="srt://127.0.0.1:9000?mode=listener&latency=80000"; C="cams/hdzero-nano90-upgrade-lens.json+cams/hdzero-nano90-stock-lens.json"; M=map_fdf-r6b-d05.npz
env "$@" SIGNAL=1 GRID=1280x720 GRID_FPS=30 GLUE=eng_linux/glue_mix.engine XFEAT=eng_linux/xfeat_fp32.engine RENDER=1 RCLIP=2 SCENE=$HOME/scenes/FDF-2026-R6b-spirula-web-dvr2.ply \
  python -u rt_track.py "$M,$U|0:0:640:360|$C,camvar/${tag}_tl" "$M,$U|640:0:640:360|$C,camvar/${tag}_tr" "$M,$U|0:360:640:360|$C,camvar/${tag}_bl" "$M,$U|640:360:640:360|$C,camvar/${tag}_br" \
  > >(while IFS= read -r l; do printf '%s %s\n' "$(date +%s.%N)" "$l"; done > camvar/$tag.log) 2>&1 &
T=$!                                                 # the tracker itself (the timestamping reader is a process substitution)
until grep -q listening camvar/$tag.log 2>/dev/null; do sleep 2; kill -0 $T 2>/dev/null || { tail -5 camvar/$tag.log; exit 1; }; done
t0=$(date +%s.%N)
ffmpeg -v error -re -ss $ss -t $dur -i ~/semi.MOV -an -c:v copy -f mpegts "srt://127.0.0.1:9000?pkt_size=1316&latency=80000"
for i in $(seq 60); do kill -0 $T 2>/dev/null || break; sleep 1; done   # it ends by itself when the stream does; writing a long run takes a while
kill -TERM $T 2>/dev/null; wait $T 2>/dev/null; sleep 1
awk -v t0=$t0 -v ss=$ss '/ lens\?? | signal o/ { printf "%6.1f s  %s\n", $1 - t0 + ss, substr($0, index($0, $2)) }' camvar/$tag.log
python - $tag <<'PY'
import json, sys, glob, numpy as np, collections
for f in sorted(glob.glob(f"camvar/{sys.argv[1]}_*.jsonl")):
    allr = [json.loads(l) for l in open(f)]; segs, cur = [], []
    for r in allr:
        if cur and r["i"] - cur[-1]["i"] > 15 * 30: segs.append(cur); cur = []
        cur.append(r)
    segs.append(cur)
    for rs in segs:
        sol = [r for r in rs if "pos" in r]
        if len(sol) < 30: continue
        inl = np.array([r["inl"] for r in sol]); how = collections.Counter(r["how"] for r in sol); p = np.array([r["pos"] for r in sol]); t = np.array([r["i"] for r in sol]) / 30.0; res = []
        for k in range(0, len(sol), 2):
            m = (np.abs(t - t[k]) <= 0.25) & (np.arange(len(sol)) != k)
            if m.sum() >= 4: res.append(np.linalg.norm(p[k, [0, 2]] - np.array([np.polyval(np.polyfit(t[m] - t[k], p[m, c], 2), 0) for c in (0, 2)])))
        lens = collections.Counter(r.get("lens") for r in rs)
        print(f"{f[7:-6]:>10s} frames {rs[0]['i']:5d}-{rs[-1]['i']:5d} solved {len(sol) / len(rs) * 100:3.0f}% | inl p50 {np.percentile(inl, 50):3.0f} | reloc {how.get('reloc', 0):3d} | wobble m p50 {np.percentile(res, 50):.2f} p90 {np.percentile(res, 90):.2f} | frames per lens {dict(lens)}")
PY
