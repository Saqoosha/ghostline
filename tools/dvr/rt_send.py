"""Stand-in for the field PC, to test the live path end to end: replay a 2x2 broadcast grid video in real time, hand
frame k to the encoder exactly at T0 + k/fps (when a capture card would deliver it), draw k as 16 black/white blocks in
the empty cell (rt_track.py FRAME_CODE reads them back, so drops and latency are exact), and send HEVC over SRT.
usage (Mac, needs srt-live-transmit): rt_send.py T0 grid.mov srt://host:9000?latency=80
env: SS (255) / DUR (75) s of the video, BR (8M), CODE (40:400: top-left of the frame-number blocks),
     ENC ("-c:v libx265 -preset ultrafast -tune zerolatency": VideoToolbox holds ~165 ms more, see realtime-tracking.ja.md),
     GST (1 = GStreamer x264 zerolatency, RTP over SRT; receive with rt_track.py GRID_GST=1), SIZE (WxH sent with GST, e.g. 854x480)"""
import sys, os, time, subprocess, numpy as np
T0, src, url = float(sys.argv[1]), sys.argv[2], sys.argv[3]
BR, SS, DUR = os.environ.get("BR", "8M"), os.environ.get("SS", "255"), os.environ.get("DUR", "75")
cx, cy = [int(v) for v in os.environ.get("CODE", "40:400").split(":")]
W, H, FPS = 1280, 720, 30
dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", SS, "-t", DUR, "-i", src, "-an", "-s", f"{W}x{H}", "-r", str(FPS), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
if os.environ.get("GST"):                           # GStreamer, RTP over SRT: ~75 ms less than ffmpeg + mpegts (rt_track.py GRID_GST)
    host, q = url.split("//")[1].split("?") if "?" in url else (url.split("//")[1], "")
    lat = q.split("latency=")[1].split("&")[0] if "latency=" in q else "80"; SZ = os.environ.get("SIZE", f"{W}x{H}").split("x")
    enc = subprocess.Popen((f"gst-launch-1.0 -q fdsrc fd=0 ! rawvideoparse width={W} height={H} format=bgr framerate={FPS}/1 ! videoconvert ! videoscale"
                            f" ! video/x-raw,width={SZ[0]},height={SZ[1]} ! x264enc tune=zerolatency speed-preset=ultrafast bitrate={int(float(BR.rstrip('M')) * 1000)}"
                            f" key-int-max={FPS} ! rtph264pay config-interval=1 pt=96 mtu=1316 ! srtsink uri=srt://{host}?mode=caller latency={lat} wait-for-connection=false").split(),
                           stdin=subprocess.PIPE)
else: enc = subprocess.Popen(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                        *os.environ.get("ENC", "-c:v libx265 -preset ultrafast -tune zerolatency").split(), "-b:v", BR, "-maxrate", BR, "-bufsize", BR,
                        "-g", str(FPS), "-bf", "0", "-flush_packets", "1", "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", "udp://127.0.0.1:5000?pkt_size=1316"], stdin=subprocess.PIPE)
relay = subprocess.Popen(["sleep", "1"]) if os.environ.get("GST") else subprocess.Popen(["srt-live-transmit", "-q", "udp://127.0.0.1:5000", url])   # the Homebrew ffmpeg has no SRT
k, late = 0, []
while True:
    buf = dec.stdout.read(W * H * 3)
    if len(buf) < W * H * 3: break
    img = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
    for b in range(16): img[cy:cy + 40, cx + b * 36:cx + b * 36 + 32] = 255 if (k >> b) & 1 else 0
    wait = T0 + k / FPS - time.time()
    if wait > 0: time.sleep(wait)
    else: late.append(-wait)
    enc.stdin.write(img.tobytes()); k += 1
enc.stdin.close(); enc.wait(); time.sleep(2); relay.terminate()
print(f"SENT {k} frames; handed over late {len(late)} times (max {max(late or [0]) * 1000:.0f} ms)")
