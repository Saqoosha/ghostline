"""UDGP 2026 R3: one pilot's cell of a ZowieBox 2x2 recording, straight from the original file, as the tracker's pinhole video.
The cell (the 4:3 HDZero fisheye stretched over 960x540, picture in the top 522 rows) is stretched back to the camera
definition's 960x720 and undistorted to its pinhole, every frame, written near-losslessly (x264 crf 8): the tracker
(rt_track.py) reads <out>.mp4 with <out>.json beside it (the camera definition and the fps).
  uv run --with opencv-python-headless --with numpy python tools/dvr/udgp_pinhole.py <original.mp4> <start s> <dur s> <slot 0-3> <cam.json> <out stem>
The original is never re-encoded on the way: its frames are decoded here once."""
import json, subprocess, sys
import cv2, numpy as np

src, t0, dur, slot, camf, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4]), sys.argv[5], sys.argv[6]
cam = json.load(open(camf)); W, H = cam["width"], cam["height"]
fk = cam["source_fisheye"]; Kf = np.array([[fk["fx"], 0, fk["cx"]], [0, fk["fy"], fk["cy"]], [0, 0, 1]])
K = np.array([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
mx, my = cv2.fisheye.initUndistortRectifyMap(Kf, np.array(fk["k"]), np.eye(3), K, (W, H), cv2.CV_32FC1)
x0, y0, cw, ch = (slot % 2) * 960, (slot // 2) * 540, 960, 522
# pinhole pixel -> fisheye pixel (W x H) -> the cell's pixel in the 1920x1080 grid
mx, my = (mx * cw / W + x0).astype(np.float32), (my * ch / H + y0).astype(np.float32)
probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", src],
                       capture_output=True, text=True, check=True).stdout.strip()
n, d = map(int, probe.split("/")); fps = n / d
dec = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t0:.3f}", "-i", src, "-t", f"{dur:.3f}", "-map", "0:v:0",
                        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
enc = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", f"{fps:.6f}",
                        "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "8", "-pix_fmt", "yuv444p", out + ".mp4"], stdin=subprocess.PIPE)
fb, i = 1920 * 1080 * 3, 0
while True:
    buf = dec.stdout.read(fb)
    if len(buf) < fb: break
    fr = np.frombuffer(buf, np.uint8).reshape(1080, 1920, 3)
    enc.stdin.write(cv2.remap(fr, mx, my, cv2.INTER_LINEAR).tobytes()); i += 1
enc.stdin.close(); enc.wait(); dec.wait()
json.dump(dict(cam, fps=fps, source=src, start_s=t0, slot=slot), open(out + ".json", "w"), indent=1)
print(out, i, "frames at", round(fps, 3), "fps")
