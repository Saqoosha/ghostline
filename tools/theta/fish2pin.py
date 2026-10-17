"""One THETA V lens video (1920x1920 fisheye, split from the dual-fisheye) -> a pinhole video rt_track.py can read.
  fish2pin.py <lens.mp4> <sparse dir> <camera id> <roll deg> <out.mp4> [hfov 100]
The fisheye intrinsics come from the SfM (COLMAP cameras.bin, OPENCV_FISHEYE). roll turns the view about the optical
axis so the ground is at the bottom (THETA V lenses lie on their side). Writes out.mp4 and out.mp4.json."""
import sys, json, struct, math, subprocess, numpy as np, cv2

src, sparse, cid, roll, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
hfov = float(sys.argv[6]) if len(sys.argv) > 6 else 100.0
NP = {0: 3, 1: 4, 2: 4, 3: 5, 4: 8, 5: 8, 6: 12, 7: 5, 8: 4, 9: 5, 10: 12}

def cameras(path):
    cams = {}
    with open(path, "rb") as f:
        n, = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            i, model, w, h = struct.unpack("<iiQQ", f.read(24))
            cams[i] = (model, w, h, struct.unpack("<%dd" % NP[model], f.read(8 * NP[model])))
    return cams

model, w, h, p = cameras(f"{sparse}/cameras.bin")[cid]
assert model == 5, f"camera {cid} is model {model}, not OPENCV_FISHEYE"
fx, fy, cx, cy, k1, k2, k3, k4 = p
cap = cv2.VideoCapture(src); fps = cap.get(cv2.CAP_PROP_FPS)
W, H = 960, 720; f = W / 2 / math.tan(math.radians(hfov) / 2)
u, v = np.meshgrid(np.arange(W) + 0.5 - W / 2, np.arange(H) + 0.5 - H / 2)
r = math.radians(roll); c, s = math.cos(r), math.sin(r)
x, y = (c * u - s * v) / f, (s * u + c * v) / f          # the pinhole ray, turned about z into the fisheye frame
rr = np.hypot(x, y); th = np.arctan(rr)
td = th * (1 + k1 * th**2 + k2 * th**4 + k3 * th**6 + k4 * th**8)
sc = np.where(rr > 1e-9, td / np.maximum(rr, 1e-9), 1.0)
mx = (fx * x * sc + cx - 0.5).astype(np.float32); my = (fy * y * sc + cy - 0.5).astype(np.float32)
if cap.get(cv2.CAP_PROP_FRAME_WIDTH) != w:               # SfM ran on images of another size
    k = cap.get(cv2.CAP_PROP_FRAME_WIDTH) / w; mx, my = (mx + 0.5) * k - 0.5, (my + 0.5) * k - 0.5
enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", f"{fps}",
                        "-i", "-", "-c:v", "h264_nvenc", "-cq", "18", "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
n = 0
while True:
    ok, im = cap.read()
    if not ok: break
    enc.stdin.write(cv2.remap(im, mx, my, cv2.INTER_LINEAR).tobytes()); n += 1
enc.stdin.close(); enc.wait()
json.dump(dict(width=W, height=H, fx=f, fy=f, cx=W / 2, cy=H / 2, hfov_deg=hfov, fps=fps, t0=0, roll_deg=roll,
               source_fisheye=dict(fx=fx, fy=fy, cx=cx, cy=cy, k=[k1, k2, k3, k4], size=[w, h])), open(out + ".json", "w"), indent=1)
print(f"{n} frames, {fps:.3f} fps -> {out}")
