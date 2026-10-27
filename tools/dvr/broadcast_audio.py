"""The event's broadcast sound for each heat's replay page (viewer/src/aud/broadcast.ts): the stream's audio track cut to the
heat, MARGIN s either side, as <out>/<heat id>/broadcast.m4a, with broadcast.json {"audio": "broadcast.m4a", "t0": <ms>}: the
wall time the cut's 0 s plays. The page sets the sound to (replay wall time - t0), so the heat's own start (race.json or
live.json, a few ms apart) does not matter.
  yt-dlp -f 140 -o <id>.m4a <video id>       (the audio track; its 0 s is the video's)
  python broadcast_audio.py <audio> <map.json> <heats.json> <out dir> [heat id ...]
The map is broadcast_sync.py's: the stretches of the stream with one wall time for its 0 s (a YouTube archive drops the
minutes its encoder was cut off, and YouTube's release time was 8 s off besides). A heat is cut only when it lies inside one
stretch, short of the next one's first measured minute: across a drop its sound would jump. A map of one stretch may be
widened by hand (JDL R7 10/25's finals: 0 s = 1792897956041, measured 13:03-14:24, used from the stream's start)."""
import json, os, subprocess, sys
src, mp, heats, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
only = set(sys.argv[5:]); MARGIN = 5.0
segs = sorted(json.load(open(mp)), key=lambda s: s["from"])
dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src], capture_output=True, text=True, check=True).stdout)
n = 0
for h in json.load(open(heats))["heats"]:
    if only and h["id"] not in only: continue
    for i, s in enumerate(segs):                    # the stretch the heat's start falls in, and its bounds: the neighbours' measured ends
        lo = segs[i - 1]["to"] if i else 0.0; hi = segs[i + 1]["from"] if i + 1 < len(segs) else dur
        a, b = (h["start"] - s["t0"]) / 1000 - MARGIN, (h["end"] - s["t0"]) / 1000 + MARGIN
        if lo <= a and b <= hi and s["from"] - 600 <= a and b <= s["to"] + 600: break
    else: continue                                   # not in the stream, or across a drop
    d = os.path.join(out, h["id"]); os.makedirs(d, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-i", src, "-t", f"{b - a:.3f}", "-vn", "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", os.path.join(d, "broadcast.m4a")], check=True)   # re-encoded: a copy cuts at packet edges
    json.dump({"audio": "broadcast.m4a", "t0": s["t0"] + round(a * 1000)}, open(os.path.join(d, "broadcast.json"), "w")); n += 1
    print(f"{h['id']} {h.get('cls') or ''}: {a:.1f}-{b:.1f} s", flush=True)
print(f"{n} heats -> {out}")
