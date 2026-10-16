"""Split a MultiGP pilot-review "8up" recording into per-pilot frames for SfM.

The recording is 1920x1080, a 3x3 grid of 640x360 cells; each feed is 4:3 (480x360) centred in
its cell. Per slot we sample frames at FPS, drop frames where the feed is black, frozen (crashed
quad) or static (lost link), and write a COLMAP mask that hides the OSD: pixels whose value barely
changes over the whole flight are overlay text, not the world.

Two passes over the video so memory stays small (a 30 fps race is 10,000 frames x 6 MB): the first
accumulates per-slot statistics and decides which frames are live, the second writes those frames.

usage: extract.py <video> <out_dir> <t0> <t1> [fps]
writes <out_dir>/images/<race>_s<slot>/<frame>.jpg and <out_dir>/masks/... (<name>.jpg.png)
"""
import os, subprocess, sys
import numpy as np, cv2

src, out, t0, t1 = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
FPS = float(sys.argv[5]) if len(sys.argv) > 5 else 5
race = os.environ.get("RACE", os.path.splitext(os.path.basename(src))[0])
W, H, CW, CH, FW = 1920, 1080, 640, 360, 480
BAR = 42    # race overlay across the top of each cell: place, pilot, lap, lap time
MED_EVERY = 10   # every n-th live frame is kept for the median (prop mask)
LAG = max(1, round(FPS / 3))   # frames in 1/3 s: the freeze test compares frames this far apart


def frames():
    rd = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", str(t0), "-t", str(t1 - t0), "-i", src,
                           "-vf", f"fps={FPS}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    while True:
        buf = rd.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    rd.wait()


def cell(f, slot):
    r, c = divmod(slot, 3)
    x0, y0 = c * CW + (CW - FW) // 2, r * CH
    return f[y0:y0 + CH, x0:x0 + FW]


# ---- pass 1: per-slot statistics
S = [dict(bright=[], diff=[], hf=[], prev=[], n=0, s1=np.zeros((CH, FW), np.float64), s2=np.zeros((CH, FW), np.float64), med=[]) for _ in range(9)]
N = 0
for f in frames():
    N += 1
    for slot in range(9):
        s = S[slot]; g = cell(f, slot).mean(2).astype(np.float32)
        s["bright"].append(g.mean())
        # frozen = crashed / no signal: almost no change against the previous sample. Compare 8x8
        # block means so a crashed quad's flickering close-up (codec noise) does not count as motion
        gb = g[BAR:BAR + 312].reshape(39, 8, 60, 8).mean((1, 3))
        # against the frame 1/3 s earlier, not the previous one: the threshold was set at 3 fps, and at 30-60 fps
        # neighbouring frames of a flying quad differ too little and were dropped as frozen
        s["diff"].append(np.inf if len(s["prev"]) < LAG else np.abs(gb - s["prev"][-LAG]).mean())
        s["prev"] = (s["prev"] + [gb])[-LAG:]
        # analog-style static after a lost link: neighbouring pixels are uncorrelated
        s["hf"].append(np.abs(np.diff(g[BAR:], axis=1)).mean())
print("frames", N)

live = {}
for slot in range(9):
    s = S[slot]; bright, diff, hf = (np.array(s[k]) for k in ("bright", "diff", "hf"))
    if (bright > 30).mean() < 0.2:
        continue
    lv = (bright > 30) & (diff > 6.0) & (hf < 12)
    if lv.sum() >= 10:
        live[slot] = lv
print("live", {k: int(v.sum()) for k, v in live.items()})

# ---- pass 2: accumulate the OSD statistics over live frames, keep a median subsample, write the frames
i = 0
for f in frames():
    for slot, lv in live.items():
        if not lv[i]:
            continue
        s = S[slot]; im = cell(f, slot); g = im.mean(2)
        s["s1"] += g; s["s2"] += g * g; s["n"] += 1
        if s["n"] % MED_EVERY == 1:
            s["med"].append(im.copy())
        name = f"{race}_s{slot}"
        os.makedirs(f"{out}/images/{name}", exist_ok=True)
        fn = f"{int(round((t0 + (i + 0.5) / FPS) * 1000)):07d}.jpg"   # the fps filter samples mid-interval
        cv2.imwrite(f"{out}/images/{name}/{fn}", im, [cv2.IMWRITE_JPEG_QUALITY, 95])
    i += 1

# ---- masks (one per slot, copied for every frame)
for slot, lv in live.items():
    s = S[slot]; n = s["n"]
    std = np.sqrt(np.maximum(s["s2"] / n - (s["s1"] / n) ** 2, 0))
    osd = (std < 12).astype(np.uint8)                       # OSD: low temporal std over the live frames
    med = np.median(np.stack(s["med"]), 0).astype(np.uint8)  # props: fixed in the lower corners, saturated, not grass-green
    hsv = cv2.cvtColor(med, cv2.COLOR_BGR2HSV)
    corner = (np.arange(CH)[:, None] > CH * 0.6) & (np.abs(np.arange(FW)[None] - FW / 2) > FW * 0.22)
    osd |= ((hsv[..., 1] > 90) & ((hsv[..., 0] < 30) | (hsv[..., 0] > 85)) & corner).astype(np.uint8)
    osd = cv2.dilate(osd, np.ones((9, 9), np.uint8))
    mask = np.where(osd > 0, 0, 255).astype(np.uint8)
    yy, xx = np.mgrid[:CH, :FW]
    mask[((xx - FW / 2) / (FW * 0.62)) ** 2 + ((yy - CH / 2) / (CH * 0.62)) ** 2 > 1] = 0   # dark fisheye corners
    mask[:BAR] = 0
    name = f"{race}_s{slot}"
    os.makedirs(f"{out}/masks/{name}", exist_ok=True)
    for fn in os.listdir(f"{out}/images/{name}"):
        cv2.imwrite(f"{out}/masks/{name}/{fn}.png", mask)
    print(name, "live", n, "/", N, "osd px", int((mask == 0).sum()))
