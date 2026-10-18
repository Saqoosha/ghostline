"""Full-resolution copies (4-up feeds: 720x540) of the frames extract.py kept at 480x360 for the localizer: the same
sample times and names. For night feeds, where ALIKED found more matches at full size (night_sfm_hloc.py).
usage: fullres_frames.py <4-up recording> <extract.py out dir (images/<race>_s<slot>)> <out dir> [fps]"""
import os, subprocess, sys
import numpy as np, cv2

src, ex, out = sys.argv[1:4]
FPS = float(sys.argv[4]) if len(sys.argv) > 4 else 15
W, H, GCW, GCH, GFW = 1920, 1080, 960, 540, 720
cells = {}
for c in sorted(os.listdir(f"{ex}/images")):
    cells[int(c.rsplit("_s", 1)[1])] = (c, set(os.listdir(f"{ex}/images/{c}")))
    os.makedirs(f"{out}/{c}", exist_ok=True)
rd = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-vf", f"fps={FPS}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                      stdout=subprocess.PIPE)
i = n = 0
while True:
    buf = rd.stdout.read(W * H * 3)
    if len(buf) < W * H * 3:
        break
    f = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    fn = f"{int(round((i + 0.5) / FPS * 1000)):07d}.jpg"           # extract.py's names: mid-interval
    for k, (c, names) in cells.items():
        if fn in names:
            r, cc = divmod(k, 2)
            x0, y0 = cc * GCW + (GCW - GFW) // 2, r * GCH
            cv2.imwrite(f"{out}/{c}/{fn}", f[y0:y0 + GCH, x0:x0 + GFW], [cv2.IMWRITE_JPEG_QUALITY, 95])
            n += 1
    i += 1
rd.wait()
print(f"{n} frames of {sum(len(v[1]) for v in cells.values())} from {i} samples -> {out}")
