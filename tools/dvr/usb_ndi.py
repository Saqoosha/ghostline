# usb_ndi.py [device] [name]: the USB capture on this machine (the Event VRX's 2x2 HDMI into a Guermok) as a 1920x1080 60 fps
# NDI source, so the control page (rt_control.py) can start the tracker on it and rt_rec.py records it, as with the venue's NDI.
# Same sender as ndi_send.py; the capture is read as MJPEG (its YUYV at 1080p is 5 fps).
import sys, time, cv2, numpy as np
from fractions import Fraction
from cyndilib.sender import Sender
from cyndilib.video_frame import VideoSendFrame
from cyndilib.wrapper.ndi_structs import FourCC
dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/video0"; name = sys.argv[2] if len(sys.argv) > 2 else "USB"
cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")); cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
cap.set(cv2.CAP_PROP_FPS, 60); cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
if not cap.isOpened(): sys.exit(f"cannot open {dev}")
print(f"{dev}: {cap.get(cv2.CAP_PROP_FRAME_WIDTH):.0f}x{cap.get(cv2.CAP_PROP_FRAME_HEIGHT):.0f} {cap.get(cv2.CAP_PROP_FPS):g} fps -> NDI '{name}'", flush=True)
vf = VideoSendFrame(); vf.set_resolution(1920, 1080); vf.set_frame_rate(Fraction(60, 1)); vf.set_fourcc(FourCC.BGRX)
s = Sender(name, clock_video=False); s.set_video_frame(vf)   # the capture sets the pace
buf = np.empty((1080, 1920, 4), np.uint8); buf[..., 3] = 255; t0 = time.time(); n = 0
with s:
    while True:
        ok, f = cap.read()
        if not ok: sys.exit(f"{dev}: read failed after {n} frames")   # the service restarts it
        buf[..., :3] = f if f.shape[:2] == (1080, 1920) else cv2.resize(f, (1920, 1080), interpolation=cv2.INTER_LINEAR)
        s.write_video(buf.ravel()); n += 1
        if n % 1800 == 0: print(f"{n} frames, {n / (time.time() - t0):.1f} fps", flush=True)
