"""Find the takeoff frame from the DVR alone and hold every frame before it at one pose.

On the launch pad the image barely changes (props and video noise only), so frame-to-frame
difference sits at a low floor; lifting off multiplies it. Takeoff = the first frame whose
10-frame mean difference exceeds FACTOR x the floor and stays above it for HOLD frames. The floor
is the median over the first seconds, skipping the frozen frames some recordings start with.
Before takeoff the drone does not move, but the solved poses cannot be trusted there: the pad
view is near-field grass and a few far flags, the tracker rarely locks, and the bundle adjustment's
motion prior extrapolates the first locked frames backwards in a straight line (FDF R6b d05: 153 of
154 pad frames were motion-prior fill and wandered up to 31 m; the one pad frame that did lock sat
35 m from the first frames after takeoff, a match to a look-alike view). So the pad is placed from
the flight instead: a cubic from rest at the takeoff frame, fitted to the first FIT_S seconds of the first run of
MIN_RUN frames solved on their own points, cross-faded into the solve over its last BLEND frames. Every frame before
takeoff is held at the cubic's start with that run's first rotation, src "ground"; the fitted stretch is "takeoff".

A pad.json next to the video ({"pos", "quat"}, written by the viewer's pad tool from the first DVR frame, which shows
the pad) replaces the fitted pad: the pad frames take its pose and the cubic is fitted with its start held there. The
fit alone can be metres off (KNT: the first solved run is 0.4 s into a race launch). pad_refine.py's pad_refined.json
is used in its place while it was refined from the pad.json that is there now.
usage: takeoff.py dvr_pinhole.mp4 poses_in.json poses_out.json   (env FACTOR 2.5, HOLD 30, FLOOR_S 2, FIT_S 0.6, MIN_RUN 10, BLEND 15, PAD)
Checked on FDF R6b d05 against the blackbox: throttle leaves idle at #150, this finds #154.
"""
import json, os, sys
import cv2, numpy as np
from scipy.spatial.transform import Rotation as Rot, Slerp

video, src, dst = sys.argv[1:4]
FACTOR, HOLD, FLOOR_S = float(os.environ.get("FACTOR", 2.5)), int(os.environ.get("HOLD", 30)), float(os.environ.get("FLOOR_S", 2))
FIT_S = float(os.environ.get("FIT_S", 0.6))
BLEND = int(os.environ.get("BLEND", 15))
MIN_RUN = int(os.environ.get("MIN_RUN", 10))
J = json.load(open(src)); P = J["poses"]; fps = J["fps"]

cap = cv2.VideoCapture(video); prev = None; d = []
while True:
    ok, f = cap.read()
    if not ok:
        break
    g = cv2.cvtColor(cv2.resize(f, (240, 180)), cv2.COLOR_BGR2GRAY).astype(np.float32)[25:155]   # OSD bands out
    d.append(0.0 if prev is None else float(np.abs(g - prev).mean())); prev = g
d = np.array(d)
live = d[: int(FLOOR_S * fps * 3)]; live = live[live > 0.05]            # frozen leading frames read as 0
floor = float(np.median(live[: int(FLOOR_S * fps)]))
m = np.convolve(d, np.ones(10) / 10, "same")
above = m > FACTOR * floor
take = next(i for i in range(len(d) - HOLD) if above[i:i + HOLD].all())
print(f"floor {floor:.2f}, takeoff at #{take} ({take / fps:.2f} s into the clip)")

# Anchor on the first frame that starts a run of MIN_RUN solved frames, not on the first solved frame: right after
# takeoff the tracker sees grass and far trees, and a lone "solved" frame there can point the wrong way (d05 #161-#162:
# 5.8 m/s in +x while the flight left in -x from #176; the curve met it and drew a hook going backwards).
ok = [bool(p) and p["src"] == "ba" for p in P]
start = next(i for i in range(take, len(P) - MIN_RUN) if all(ok[i:i + MIN_RUN]))
# Right after takeoff the solved frames are weak (near-field grass): on d05 #178-#190 they swing sideways at +-55 m/s^2
# while the blackbox reads 0-2 m/s^2 across and under 22 deg/s - a straight launch. Meeting one frame exactly, even in
# velocity, carries that swing into the curve and leaves an acceleration step (an S at the launch). So a cubic from
# rest, p(t) = p0 + b t^2 + c t^3 (t from takeoff), is fitted by least squares to the solved frames of the first
# FIT_S seconds of the run, replaces every frame up to the window's end, and hands over to the solve by a cross-fade
# over the last BLEND frames. No corner anywhere; a real turn inside the first FIT_S seconds would be flattened.
end = start + int(FIT_S * fps)
fit = [i for i in range(start, end + 1) if ok[i]]
tt = (np.array(fit) - take) / fps; X = np.array([P[i]["pos"] for i in fit])
PAD = os.environ.get("PAD") or os.path.join(os.path.dirname(os.path.abspath(video)), "pad.json")
pad = json.load(open(PAD)) if os.path.exists(PAD) else None
REF = os.path.join(os.path.dirname(PAD), "pad_refined.json")        # pad_refine.py: the hand-placed pad matched finer
if pad and os.path.exists(REF):
    ref = json.load(open(REF))
    if ref["hint"]["pos"] == pad["pos"] and ref["hint"]["quat"] == pad["quat"]: pad, PAD = ref, REF
    else: print(f"{REF} was refined from an earlier pad.json: not used")
A = np.c_[np.ones_like(tt), tt ** 2, tt ** 3]
if pad:                                                              # start held at the pad the human placed
    p0 = np.array(pad["pos"], float); bc, *_ = np.linalg.lstsq(A[:, 1:], X - p0, rcond=None); coef = np.vstack([p0, bc])
else:
    coef, *_ = np.linalg.lstsq(A, X, rcond=None)
pos = coef[0]
curve = lambda t: coef[0] + coef[1] * t * t + coef[2] * t ** 3
rot = Rot.from_quat(pad["quat"] if pad else P[start]["quat"])
resid = np.linalg.norm(A @ coef - X, axis=1)
print(f"pad from {PAD}" if pad else "pad fitted from the flight")
print(f"first run of {MIN_RUN} solved frames after takeoff starts at #{start}; pad {np.round(pos, 2).tolist()}, "
      f"curve to #{end}, residual to the solved frames p50 {np.median(resid):.2f} max {resid.max():.2f} m")
for p in P[:take]:
    if p:
        p.update(pos=np.round(pos, 3).tolist(), quat=np.round(rot.as_quat(), 6).tolist(), src="ground")
n_fill = 0
for i in range(take, end + 1):
    if not P[i]:
        continue
    c = curve((i - take) / fps); w = float(np.clip((i - (end - BLEND)) / BLEND, 0, 1))
    if i < start or not ok[i]:
        w = 0.0                                                    # nothing solved to blend towards
    P[i]["pos"] = np.round((1 - w) * c + w * np.array(P[i]["pos"]), 3).tolist()
    if i < start:
        # turn from the pad's rotation to the first solved frame's, easing out of rest (smoothstep). Held at the pad's
        # until then, the whole difference landed on one frame: KNT's hand-placed pad, 35 deg at #100 -> #101.
        u = (i - take) / max(start - take, 1); u = u * u * (3 - 2 * u)
        P[i]["quat"] = np.round(Slerp([0, 1], Rot.concatenate([rot, Rot.from_quat(P[start]["quat"])]))(u).as_quat(), 6).tolist()
    if w < 1:
        P[i]["src"] = "takeoff"; n_fill += 1
print(f"{take} frames before takeoff held on the pad; {n_fill} frames after it on the takeoff curve")
J["takeoff"] = take
json.dump(J, open(dst, "w"), separators=(",", ":"))
