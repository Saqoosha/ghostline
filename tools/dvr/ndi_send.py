# ndi_send.py <video> <name> [seconds] [start s] [fps]: a 2x2 grid video as a 1920x1080 NDI source (stand-in for the EventVRX output).
# fps 60 sends every frame of a 30 fps video twice: the Event VRX's rate, for testing the tracker's thinning (MAXFPS)
import sys, time, cv2, numpy as np
from fractions import Fraction
from cyndilib.sender import Sender
from cyndilib.video_frame import VideoSendFrame
from cyndilib.wrapper.ndi_structs import FourCC
src, name = sys.argv[1], sys.argv[2]; dur = float(sys.argv[3]) if len(sys.argv) > 3 else 1e9; fps = int(sys.argv[5]) if len(sys.argv) > 5 else 30; rep = max(1, fps // 30)
vf = VideoSendFrame(); vf.set_resolution(1920, 1080); vf.set_frame_rate(Fraction(fps, 1)); vf.set_fourcc(FourCC.BGRX)
s = Sender(name, clock_video=True); s.set_video_frame(vf)
cap = cv2.VideoCapture(src); cap.set(cv2.CAP_PROP_POS_MSEC, float(sys.argv[4]) * 1000 if len(sys.argv) > 4 else 0); buf = np.empty((1080, 1920, 4), np.uint8); buf[..., 3] = 255; t0 = time.time(); n = 0
with s:
    while time.time() - t0 < dur:
        ok, f = cap.read()
        if not ok: break
        buf[..., :3] = cv2.resize(f, (1920, 1080), interpolation=cv2.INTER_LINEAR)
        for _ in range(rep): s.write_video(buf.ravel()); n += 1
print(f"sent {n} frames in {time.time() - t0:.1f} s", flush=True)
