"""Find HDZero dropout blocks, paint them over, and write the pinhole video and per-frame masks the matchers read.

A dropout replaces whole on-air blocks with junk. In 540p modes the on-air picture is 720x540 in 8x8 blocks; the
goggle scales it by 4/3 to 960x720, so in the recording a block is 10.67 px square on a grid anchored at the active
picture's origin. Measured, not assumed: on KNT's noisy frames the step between neighbouring pixels peaks exactly at
multiples of 32/3 px in both directions and is flat on clean frames; on the DVR's no-signal frames the picture repeats
every 21.33 rows (16 x 4/3). A 720p60 camera (960x720 on air) would have BLOCK=8.

A block is taken as junk when it is unlike every one of its four neighbours (its mean colour) and its own border is a
much larger step than the texture inside it. Measured on KNT: clean frames flag ~1% (a few gate stripe cells),
confetti in the sky is caught; a frame that is noise almost everywhere is under-counted (its neighbours are junk too),
and there is nothing to match in such a frame anyway.

Junk blocks are painted over from their surroundings (cv2.inpaint) before undistortion, so MASt3R sees a plausible
picture instead of confetti, and the block mask is carried through the same undistortion so the matchers can drop
any correspondence that lands on a painted block.

usage: hdz_blocks.py <source .ts/.mp4> <out dir> <t0> <dur>    env: FPS (60), BLOCK (10.6667), X0 (160: active picture's
       left edge in the 1280-wide frame), HFOV (100)
writes <out dir>/dvr_pinhole_clean.mp4 and <out dir>/noise_masks.npz (bool [frames, 180, 240], pinhole space)"""
import os, subprocess, sys, json
import cv2, numpy as np

src, out, t0, dur = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
FPS, BLK, X0, HFOV = int(os.environ.get("FPS", 60)), float(os.environ.get("BLOCK", 32 / 3)), int(os.environ.get("X0", 160)), float(os.environ.get("HFOV", 100))
W, H = 960, 720
# the undistortion undistort.py uses (same calibration, same pinhole)
K = np.array([[396.72252152857959, 0, 480], [0, 395.59635839540539, 360], [0, 0, 1]])
D = np.array([0.079083628685813145, -0.0031366574031255509, 0.012172993244533522, -0.019683516074227709])
fx = (W / 2) / np.tan(np.radians(HFOV / 2)); Kn = np.array([[fx, 0, W / 2], [0, fx, H / 2], [0, 0, 1]])
m1, m2 = cv2.fisheye.initUndistortRectifyMap(K, D, np.eye(3), Kn, (W, H), cv2.CV_16SC2)

# block index of every pixel, and which pixels sit on a block's own border
xs, ys = np.arange(W), np.arange(H)
bx, by = np.floor(xs / BLK).astype(int), np.floor(ys / BLK).astype(int); NBX, NBY = bx[-1] + 1, by[-1] + 1
bid = by[:, None] * NBX + bx[None, :]
inx = np.r_[False, bx[1:] == bx[:-1]] & np.r_[bx[:-1] == bx[1:], False]; iny = np.r_[False, by[1:] == by[:-1]] & np.r_[by[:-1] == by[1:], False]
interior = (iny[:, None] & inx[None, :]).ravel()
left_edge = np.r_[False, bx[1:] != bx[:-1]]; top_edge = np.r_[False, by[1:] != by[:-1]]
NB = NBX * NBY
def junk_blocks(bgr):
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float64); L = lab[..., 0]
    ids = bid.ravel()[interior]; px = lab.reshape(-1, 3)[interior]
    n = np.bincount(ids, minlength=NB).astype(float) + 1e-9
    mean = np.stack([np.bincount(ids, px[:, k], NB) for k in range(3)], 1) / n[:, None]
    var = np.stack([np.bincount(ids, px[:, k] ** 2, NB) for k in range(3)], 1) / n[:, None] - mean ** 2
    # step across each block's left/top border against the step between neighbours inside it
    dxv = np.abs(np.diff(L, axis=1)); dyv = np.abs(np.diff(L, axis=0))
    e_sum = np.bincount(bid[:, 1:][:, left_edge[1:]].ravel(), dxv[:, left_edge[1:]].ravel(), NB) + \
            np.bincount(bid[1:, :][top_edge[1:], :].ravel(), dyv[top_edge[1:], :].ravel(), NB)
    e_n = np.bincount(bid[:, 1:][:, left_edge[1:]].ravel(), minlength=NB) + np.bincount(bid[1:, :][top_edge[1:], :].ravel(), minlength=NB)
    same = ~left_edge[1:]; i_sum = np.bincount(bid[:, 1:][:, same].ravel(), dxv[:, same].ravel(), NB); i_n = np.bincount(bid[:, 1:][:, same].ravel(), minlength=NB)
    step = (e_sum / np.maximum(e_n, 1)) / (i_sum / np.maximum(i_n, 1) + 1e-3)
    m = mean.reshape(NBY, NBX, 3); pad = np.pad(m, ((1, 1), (1, 1), (0, 0)), mode="edge")
    nb = np.stack([pad[:-2, 1:-1], pad[2:, 1:-1], pad[1:-1, :-2], pad[1:-1, 2:]]); dmin = np.linalg.norm(nb - m[None], axis=-1).min(0)
    return (dmin > 10) & (step.reshape(NBY, NBX) > 2.0)

os.makedirs(out, exist_ok=True)
rd = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", str(t0), "-t", str(dur), "-i", src, "-vf", f"crop={W}:{H}:{X0}:0",
                       "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
wr = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                       "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                       f"{out}/dvr_pinhole_clean.mp4"], stdin=subprocess.PIPE)
masks, share = [], []
while True:
    buf = rd.stdout.read(W * H * 3)
    if len(buf) < W * H * 3: break
    img = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
    jb = junk_blocks(img); pm = jb[by[:, None], bx[None, :]].astype(np.uint8)
    if pm.any(): img = cv2.inpaint(img, cv2.dilate(pm, np.ones((3, 3), np.uint8)), 3, cv2.INPAINT_TELEA)
    wr.stdin.write(cv2.remap(img, m1, m2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT).tobytes())
    pin = cv2.remap(pm * 255, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT) > 0
    masks.append(cv2.resize(pin.astype(np.uint8), (240, 180), interpolation=cv2.INTER_AREA) > 0); share.append(jb.mean())
wr.stdin.close(); wr.wait(); rd.wait()
np.savez_compressed(f"{out}/noise_masks.npz", masks=np.array(masks))
s = np.array(share); print(f"{len(s)} frames; junk blocks per frame p50 {np.median(s)*100:.1f}% p90 {np.percentile(s, 90)*100:.1f}% max {s.max()*100:.0f}%; "
                           f"frames over 5%: {np.sum(s > 0.05)}")
