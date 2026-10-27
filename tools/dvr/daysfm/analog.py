"""digital (HDZero) or analog per cell: a digital feed sits in the cell with 12 black rows at the bottom (its 4:3 picture
stretched into 640x348), an analog one fills all 360 rows. Only frames with a picture (texture) count.
usage: analog.py <clip dir>...   prints <heat> <cell> digital|analog|none"""
import os, subprocess, sys, numpy as np
for d in sys.argv[1:]:
    for c in ["R2", "F1", "R4", "R5"]:
        f = f"{d}/{c}.mp4"
        if not os.path.exists(f): continue
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", f, "-vf", "fps=1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
        F = np.frombuffer(raw, np.uint8).reshape(-1, 360, 640).astype(np.float32); F = F[F[:, :340].std((1, 2)) > 25]
        if len(F) < 5: print(os.path.basename(d), c, "none", flush=True); continue
        bottom = np.median(F[:, 349:358].mean((1, 2))); above = np.median(F[:, 330:345].mean((1, 2)))
        print(os.path.basename(d), c, "digital" if bottom < 12 and above > 25 else "analog", f"({len(F)} frames, bottom {bottom:.0f}, above {above:.0f})", flush=True)
