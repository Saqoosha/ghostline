"""Where each pilot's video sits in a 4-up recording. The Mains' 2x2 recordings do not place the feeds alike: within a
960x540 cell the picture starts 90-120 px from the left and is 640-730 px wide (2026-10-17 22:01: four cells, four
rectangles), so one fixed crop showed a black band on one side and cut the other. The picture is where the pixels
change over time: the per-pixel spread over frames sampled across the recording, above the cell's broadcast bar,
without the static pilot photo and flag in the lower corner.
usage: feed_rects.py <recording> [frames=40]   prints {slot: [x, y, w, h]} in recording pixels (JSON)"""
import json, subprocess, sys
import numpy as np

src = sys.argv[1]; N = int(sys.argv[2]) if len(sys.argv) > 2 else 40
W, H, CW, CH, BAR = 1920, 1080, 960, 540, 46
dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src]))
frames = []
for k in range(N):
    t = dur * (k + 0.5) / N
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True).stdout
    if len(raw) == W * H: frames.append(np.frombuffer(raw, np.uint8).reshape(H, W).astype(np.float32))
spread = np.std(np.stack(frames), axis=0)
out = {}
for slot in range(4):
    r, c = divmod(slot, 2)
    cell = spread[r * CH + BAR:(r + 1) * CH, c * CW:(c + 1) * CW]
    moving = cell > 12
    cols = np.where(moving.mean(axis=0) > 0.5)[0]           # columns where most of the height changes
    rows = np.where(moving[:, cols[0]:cols[-1] + 1].mean(axis=1) > 0.5)[0] if len(cols) else []
    if len(cols) < 100 or len(rows) < 100:
        continue                                             # no video in this cell (an empty slot)
    x0, x1, y0, y1 = int(cols[0]), int(cols[-1]) + 1, int(rows[0]), int(rows[-1]) + 1
    out[slot] = [c * CW + x0, r * CH + BAR + y0, (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2]   # even sizes for h264
print(json.dumps(out))
