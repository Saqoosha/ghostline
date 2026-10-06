"""rt_day_gate.py: how much of an event day carries pilots' pictures, read off the day's broadcast recording - the input for
rt_day_power.py. Two samples a second: is the screen the race layout (the red LIVE label top-left over a dark header), and does each
of the four pilot cells in the strip under the header carry a picture, by the tracker's own test (rt_track.py has_signal: a flat cell
is off, one whose neighbouring rows do not correlate is static and off).
The layout is the JDL broadcast's at 640x360 (2026 Round 6: youtu.be/pgzb9XF8WUU qualifying, youtu.be/d4ARVjP8004 finals; yt-dlp -f 134
is enough). Another broadcast needs its own CELLS and label test.
usage: rt_day_gate.py <video> <out.npy> [seconds]      # rows of [race layout, cell 1..4], bool"""
import sys, subprocess, numpy as np
W, H = 640, 360; CELLS = [(k * 159 + 4, 34, 151, 80) for k in range(4)]
def has_signal(c):
    g = (c[..., 0] * 0.299 + c[..., 1] * 0.587 + c[..., 2] * 0.114).astype(np.float32)
    if g.std() < 2: return False
    a0, a1 = g[:-1] - g[:-1].mean(), g[1:] - g[1:].mean()
    return float((a0 * a1).sum() / (np.sqrt((a0 ** 2).sum() * (a1 ** 2).sum()) + 1e-6)) >= 0.5
def race_layout(f):                                 # red on 17% of the label's box in a race frame, 0% on the other screens
    r = f[3:15, 48:88].astype(np.int16); return bool(((r[..., 0] - r[..., 1] > 60) & (r[..., 0] > 110)).mean() >= 0.12 and f[20:30].mean() < 90)
cmd = ["ffmpeg", "-v", "error"] + (["-t", sys.argv[3]] if len(sys.argv) > 3 else []) + ["-i", sys.argv[1], "-vf", "fps=2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=W * H * 3 * 8); rows = []
while True:
    b = p.stdout.read(W * H * 3)
    if len(b) < W * H * 3: break
    f = np.frombuffer(b, np.uint8).reshape(H, W, 3); rows.append([race_layout(f)] + [has_signal(f[y:y + h, x:x + w]) for x, y, w, h in CELLS])
a = np.array(rows, bool); np.save(sys.argv[2], a)
print(sys.argv[1], "samples", len(a), "hours", round(len(a) / 7200, 2), "race layout", round(a[:, 0].mean() * 100, 1), "%")
