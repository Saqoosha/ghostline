"""More anchors from the hand-clicked ones: each clicked footage point (its map 3D point known) is followed into the
neighbouring frames with optical flow, and every frame it still reaches gets a pose from those points. The footage
could not be tied together by SfM or registration; the clicked anchors are the only thing that holds.
A tracked point is kept only when tracking it back lands within 1 px of where it came from; a frame is kept only under
the anchor page's rules (6+ points within 8 px, the pose moving <= MAX_WOBBLE m without any one point); the walk in
each direction stops at the first frame that fails.
usage: anchor_spread.py <map model> <footage image dir> <anchor dir (anchors.json, camera.json)> <out.json> [max_steps=15]"""
import json, os, sys
import numpy as np, cv2
import pycolmap as p

map_dir, fdir, adir, out_path = sys.argv[1:5]
MAX_STEPS = int(sys.argv[5]) if len(sys.argv) > 5 else 15
MAX_WOBBLE = float(os.environ.get("MAX_WOBBLE", "0.5")); UNIT = 7.37
FB_PX = float(os.environ.get("FB_PX", "1.0"))           # tracked back within this of where it came from
MIN_PTS = int(os.environ.get("MIN_PTS", "6"))            # points that must agree within 8 px
M = p.Reconstruction(map_dir)
cj = json.load(open(f"{adir}/camera.json"))
cam = p.Camera(camera_id=1, model=cj["model"], width=cj["width"], height=cj["height"], params=cj["params"])
A = json.load(open(f"{adir}/anchors.json"))
gray = lambda k: cv2.imread(f"{fdir}/f{k:05d}.jpg", cv2.IMREAD_GRAYSCALE)
VIDEO = os.environ.get("VIDEO")                           # the 60 fps source; footage frame k is its frame 4 (k - 1) + 1
SUB = 4
_cap = cv2.VideoCapture(VIDEO) if VIDEO else None


def src_gray(j):
    _cap.set(cv2.CAP_PROP_POS_FRAMES, j); okk, f = _cap.read()
    return cv2.cvtColor(cv2.resize(f, (cam.width, cam.height), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)


LK = dict(winSize=(31, 31), maxLevel=4, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01))


def pose(P2, P3):
    """(cam_from_world, per-point error px, wobble m) or None."""
    o = p.AbsolutePoseEstimationOptions(); o.ransac.max_error = 12.0
    r = p.estimate_and_refine_absolute_pose(P2, P3, cam, o)
    if r is None:
        return None
    T = r["cam_from_world"]; c = -T.rotation.matrix().T @ T.translation
    err = np.linalg.norm(cam.img_from_cam((T.rotation.matrix() @ P3.T).T + T.translation) - P2, axis=1)
    shift = []
    for k in range(len(P2)):
        keep = np.arange(len(P2)) != k
        r2 = p.estimate_and_refine_absolute_pose(P2[keep], P3[keep], cam, o)
        if r2 is not None:
            T2 = r2["cam_from_world"]; shift.append(np.linalg.norm(-T2.rotation.matrix().T @ T2.translation - c) * UNIT)
    return T, err, (max(shift) if shift else 99.0)


def ok(err, wob):
    return (err <= 8).sum() >= MIN_PTS and wob <= MAX_WOBBLE


result = {}
for fly, a in sorted(A.items()):
    s = a.get("solve", {}); k0 = int(fly[1:6])
    if not s.get("ok") or (s.get("wobble_m") or 99) > MAX_WOBBLE or sum(e <= 8 for e in s["err"]) < 6:
        continue
    P2 = np.array([q[:2] for q in a["pairs"]], np.float32); P3 = np.array([M.points3D[int(q[2])].xyz for q in a["pairs"]])
    good = np.array(s["err"]) <= 8                       # follow only the points that agree at the anchor
    P2, P3 = P2[good], P3[good]
    got = pose(P2.astype(float), P3)
    if got is None or not ok(got[1], got[2]):
        continue
    T, err, wob = got
    result[fly] = dict(frame=k0, source=fly, steps=0, qvec=list(T.rotation.quat), tvec=list(T.translation), n=int(len(P2)),
                       err_p90=float(np.percentile(err, 90)), wobble_m=round(float(wob), 2))
    for d in (+1, -1):
        pts, X, prev, k = P2.copy(), P3.copy(), gray(k0), k0
        for step in range(1, MAX_STEPS + 1):
            k = k0 + d * step
            if not os.path.exists(f"{fdir}/f{k:05d}.jpg"):
                break
            # through the source video's in-between frames when it is given: at 15 fps a point moves 26-48 px a step
            # and a third of them were lost; at 60 fps it moves a quarter of that
            subs = [src_gray(4 * (k - d - 1) + 1 + d * j) for j in range(1, SUB)] if VIDEO else []
            for cur in subs + [gray(k)]:
                nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts.reshape(-1, 1, 2), None, **LK)
                back, st2, _ = cv2.calcOpticalFlowPyrLK(cur, prev, nxt, None, **LK)
                fb = np.linalg.norm(back.reshape(-1, 2) - pts, axis=1)
                keep = (st.ravel() == 1) & (st2.ravel() == 1) & (fb < FB_PX)
                pts, X, prev = nxt.reshape(-1, 2)[keep], X[keep], cur
            if len(pts) < MIN_PTS:
                break
            got = pose(pts.astype(float), X)
            if got is None or not ok(got[1], got[2]):
                break
            T, err, wob = got
            name = f"f{k:05d}.jpg"
            if name not in result or result[name]["wobble_m"] > wob:   # two anchors reach one frame: the steadier pose
                result[name] = dict(frame=k, source=fly, steps=step * d, qvec=list(T.rotation.quat), tvec=list(T.translation),
                                    n=int(len(pts)), err_p90=float(np.percentile(err, 90)), wobble_m=round(float(wob), 2))
    reach = sorted(v["frame"] for v in result.values() if v["source"] == fly)
    print(f"{fly}: frames {reach[0]}-{reach[-1]} ({len(reach)})", flush=True)
json.dump(result, open(out_path, "w"), indent=1)
print(f"{len(result)} posed frames -> {out_path}")
