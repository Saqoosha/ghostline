# ndi_send.py <video> <name> [seconds]: a 2x2 grid video as a 1920x1080 30 fps NDI source (stand-in for the EventVRX output)
import sys, time, cv2, numpy as np
from fractions import Fraction
from cyndilib.sender import Sender
from cyndilib.video_frame import VideoSendFrame
from cyndilib.wrapper.ndi_structs import FourCC
src, name = sys.argv[1], sys.argv[2]; dur = float(sys.argv[3]) if len(sys.argv) > 3 else 1e9
vf = VideoSendFrame(); vf.set_resolution(1920, 1080); vf.set_frame_rate(Fraction(30, 1)); vf.set_fourcc(FourCC.BGRX)
s = Sender(name, clock_video=True); s.set_video_frame(vf)
cap = cv2.VideoCapture(src); buf = np.empty((1080, 1920, 4), np.uint8); buf[..., 3] = 255; t0 = time.time(); n = 0
with s:
    while time.time() - t0 < dur:
        ok, f = cap.read()
        if not ok: break
        buf[..., :3] = cv2.resize(f, (1920, 1080), interpolation=cv2.INTER_LINEAR)
        s.write_video(buf.ravel()); n += 1
print(f"sent {n} frames in {time.time() - t0:.1f} s", flush=True)
