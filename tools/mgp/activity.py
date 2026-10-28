"""Print per-slot motion over time for an 8up recording, to find when each pilot is flying.
usage: activity.py <video> [fps]"""
import subprocess, sys
import numpy as np

src = sys.argv[1]
FPS = float(sys.argv[2]) if len(sys.argv) > 2 else 1
W, H = 480, 270     # downscaled 1920x1080; cells are 160x90
rd = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-vf", f"fps={FPS},scale={W}:{H}", "-f", "rawvideo",
                       "-pix_fmt", "gray", "-"], stdout=subprocess.PIPE)
prev, rows = None, []
while True:
    buf = rd.stdout.read(W * H)
    if len(buf) < W * H:
        break
    f = np.frombuffer(buf, np.uint8).reshape(H, W).astype(np.float32)
    if prev is not None:
        d = np.abs(f - prev)
        rows.append([d[r * 90:(r + 1) * 90, c * 160 + 20:c * 160 + 140].mean() for r in range(3) for c in range(3)])
    prev = f
a = np.array(rows)
for t in range(len(a)):
    print(f"{(t + 1) / FPS:6.0f}", "".join("#" if v > 6 else ("+" if v > 2 else ".") for v in a[t]))
