"""JDL 2x2 cells -> MGP-style SfM frames: one heat's cells (night_heats.py clips, 640x360 with the picture in the top 348
rows, a 4:3 feed stretched) back to 480x360 4:3 frames at FPS over [t0, t0+dur), and a mask per pilot that hides the OSD
(pixels that barely change over the flight are overlay text). usage: jdl_extract.py <heat id> <t0> <dur> <fps> <out> <cell>..."""
import os, subprocess, sys, numpy as np, cv2
hid, t0, dur, fps, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
for c in sys.argv[6:]:
    src = f"/home/saqoosha/rt/night1025/out/{hid}/{c}.mp4"; name = f"{hid}_{c}"
    if not os.path.exists(src): src = f"/home/saqoosha/rt/night1025/out_yt/{hid}/{c}.mp4"   # the quarterfinals: cut from the broadcast
    if not os.path.exists(src): print(name, "no clip"); continue
    os.makedirs(f"{out}/images/{name}", exist_ok=True); os.makedirs(f"{out}/masks/{name}", exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-ss", str(t0), "-t", str(dur), "-i", src, "-vf", f"fps={fps},crop=640:348:0:0,scale=480:360", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout; F = np.frombuffer(raw, np.uint8).reshape(-1, 360, 480, 3)
    g = F.mean(3).astype(np.float32)                                              # OSD text keeps its edges in place over the whole flight
    edge = np.stack([np.hypot(cv2.Sobel(x, cv2.CV_32F, 1, 0), cv2.Sobel(x, cv2.CV_32F, 0, 1)) > 40 for x in g[::3]]).mean(0)
    osd = (edge > 0.6) | (g.std(0) < 6)
    osd = cv2.dilate(osd.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    mask = np.where(osd, 0, 255).astype(np.uint8); mask[:4] = mask[-4:] = 0; mask[:, :4] = mask[:, -4:] = 0
    kept = 0
    for k, f in enumerate(F):
        if f.mean() < 12 or (k and np.abs(f.astype(int) - F[k - 1]).mean() < 1.0): continue   # black or frozen (a crash, lost link)
        t_ms = round((t0 + k / fps) * 1000); fn = f"{t_ms:07d}.jpg"
        cv2.imwrite(f"{out}/images/{name}/{fn}", f, [cv2.IMWRITE_JPEG_QUALITY, 95]); cv2.imwrite(f"{out}/masks/{name}/{fn}.png", mask); kept += 1
    print(name, len(F), "frames,", kept, "kept, OSD mask", int(osd.sum()), "px", flush=True)
