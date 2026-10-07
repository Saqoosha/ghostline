#!/bin/bash
# rt_camsweep.sh <start s> <seconds> <cell x:y:w:h> <sx:sy[:k1[:k2[:k3[:k4]]]]> ...: which camera definition fits the pilot in a cell?
# One stretch of the semifinal grid (~/semi.MOV, 1280x720) from this box over SRT on localhost into the same cell once per variant
# (four fit one pass): the fisheye's focal lengths scaled by sx, sy and its distortion coefficients by k1.., from the definition of
# the Nano 90 with the upgrade lens. No truth is needed: the right definition shows as more inliers, fewer relocalizations and less
# wobble (distance of an answer from a quadratic through its neighbours). This is how data/dvr/cams/hdzero-nano90-stock-lens.json was found.
ss=$1; dur=$2; rect=$3; shift 3; cd ~/rt; export PATH=$HOME/mastenv/bin:/usr/local/cuda-12.9/bin:$PATH; mkdir -p camvar; rm -f camvar/out_*
specs=()
for v in "$@"; do
  python3 - "$v" <<'PY'
import json, sys
sx, sy, *ks = (float(a) for a in sys.argv[1].split(":")); c = json.load(open("cams/hdzero-nano90-upgrade-lens.json"))   # a third number scales the distortion's k1 (a fourth k2)
c["source_fisheye"]["fx"] *= sx; c["source_fisheye"]["fy"] *= sy
for j, k in enumerate(ks): c["source_fisheye"]["k"][j] *= k
json.dump(c, open(f"camvar/cam_{sys.argv[1].replace(':', '_')}.json", "w"))
PY
  n=${v//:/_}; specs+=("map_fdf-r6b-d05.npz,srt://127.0.0.1:9000?mode=listener&latency=80000|$rect|camvar/cam_$n.json,camvar/out_$n")
done
SIGNAL=1 GRID=1280x720 GRID_FPS=30 GLUE=eng_linux/glue_mix.engine XFEAT=eng_linux/xfeat_fp32.engine RENDER=1 RCLIP=2 SCENE=$HOME/scenes/FDF-2026-R6b-spirula-web-dvr2.ply \
  python -u rt_track.py "${specs[@]}" > camvar/log.txt 2>&1 &
T=$!
until grep -q listening camvar/log.txt 2>/dev/null; do sleep 2; kill -0 $T 2>/dev/null || { tail -5 camvar/log.txt; exit 1; }; done
ffmpeg -v error -re -ss $ss -t $dur -i ~/semi.MOV -an -c:v copy -f mpegts "srt://127.0.0.1:9000?pkt_size=1316&latency=80000"
sleep 2; kill -TERM $T; wait $T
python - <<'PY'
import json, glob, numpy as np, collections
for f in sorted(glob.glob("camvar/out_*.jsonl")):
    rs = [json.loads(l) for l in open(f)]; sol = [r for r in rs if "pos" in r]
    if len(sol) < 30: print(f[11:-6], "rows", len(rs), "solved", len(sol)); continue
    inl = np.array([r["inl"] for r in sol]); how = collections.Counter(r["how"] for r in sol); p = np.array([r["pos"] for r in sol]); t = np.array([r["i"] for r in sol]) / 30.0; res = []
    for k in range(0, len(sol), 2):
        m = (np.abs(t - t[k]) <= 0.25) & (np.arange(len(sol)) != k)
        if m.sum() >= 4: res.append(np.linalg.norm(p[k, [0, 2]] - np.array([np.polyval(np.polyfit(t[m] - t[k], p[m, c], 2), 0) for c in (0, 2)])))
    print(f"{f[11:-6]:>10s}: rows {len(rs):4d} solved {len(sol):4d} ({len(sol) / len(rs) * 100:3.0f}%) | inl p10/p50/p90 {np.percentile(inl, 10):3.0f}/{np.percentile(inl, 50):3.0f}/{np.percentile(inl, 90):3.0f} | reloc {how.get('reloc', 0):3d} |"
          f" wobble m p50 {np.percentile(res, 50):.2f} p90 {np.percentile(res, 90):.2f} | height p50 {np.median(p[:, 1]):.1f}")
PY
