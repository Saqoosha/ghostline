"""HDZero DVR (960x720 4:3 fisheye, calibrated by COLMAP) -> pinhole video a browser can match.
Reads frames from ffmpeg, remaps with the OPENCV_FISHEYE model, writes h264 via ffmpeg."""
import subprocess, sys, json, numpy as np, cv2
src, dst, t0, dur, hfov = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
W, H = 960, 720
K = np.array([[396.72252152857959, 0, 480], [0, 395.59635839540539, 360], [0, 0, 1]])
D = np.array([0.079083628685813145, -0.0031366574031255509, 0.012172993244533522, -0.019683516074227709])
fx = (W / 2) / np.tan(np.radians(hfov / 2)); Kn = np.array([[fx, 0, W / 2], [0, fx, H / 2], [0, 0, 1]])
m1, m2 = cv2.fisheye.initUndistortRectifyMap(K, D, np.eye(3), Kn, (W, H), cv2.CV_16SC2)
json.dump({"width": W, "height": H, "fx": fx, "fy": fx, "cx": W / 2, "cy": H / 2, "hfov_deg": hfov, "t0": t0, "fps": 60,
           "source_fisheye": {"fx": K[0, 0], "fy": K[1, 1], "cx": 480, "cy": 360, "k": D.tolist()}}, open(dst + ".json", "w"), indent=1)
rd = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", str(t0), "-t", str(dur), "-i", src, "-vf", f"crop={W}:{H}:160:0", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
wr = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", "60", "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", dst], stdin=subprocess.PIPE)
n = 0
while True:
    buf = rd.stdout.read(W * H * 3)
    if len(buf) < W * H * 3: break
    img = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    wr.stdin.write(cv2.remap(img, m1, m2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT).tobytes()); n += 1
wr.stdin.close(); wr.wait(); rd.wait(); print("frames", n, "pinhole fx", round(fx, 1))
