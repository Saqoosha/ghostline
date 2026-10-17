"""Place chosen frames of a 1-in-STRIDE pinhole video on the old scan, starting from today's SfM pose carried over by a
coarse similarity (shape_align.py): render at that pose (and +-25 deg yaw), MASt3R match, PnP. Reuses cpr_track.py's
scene, renderer and solver by running its source up to the frames section.
  cpr_seeded.py <old.ply> <pin-s8.mp4> <scan_cameras.json> <sparse dir> <lens prefix v134/cam1> <coarse.json> <out.jsonl> [every]
Run it in the folder that holds cpr_track.py (tools/dvr, or ~/rt on rt4090). The video's .json is fish2pin.py's with "stride" (8 for
every 8th frame of the lens video, as the SfM frames) added."""
import sys, json, struct, math, numpy as np
ply, video, scan_json, sparse, prefix, coarse, out = sys.argv[1:8]; EVERY = int(sys.argv[8]) if len(sys.argv) > 8 else 10
src = open("cpr_track.py").read(); src = src[:src.index("# ---- frames")]
sys.argv = [sys.argv[0], ply, video, scan_json, "/tmp/cpr_seeded_scratch"]; exec(src)
qvalid[:] = True                                   # cpr_track masks the HDZero OSD through the DVR fisheye; not here
cam = json.load(open(video + ".json")); stride, roll = cam["stride"], math.radians(cam["roll_deg"])
Rz = np.array([[math.cos(roll), -math.sin(roll), 0], [math.sin(roll), math.cos(roll), 0], [0, 0, 1]])
co = json.load(open(coarse)); S, RS, tS = co["scale"], np.array(co["R"]), np.array(co["t"])
pose = {}
with open(f"{sparse}/images.bin", "rb") as f:
    n, = struct.unpack("<Q", f.read(8))
    for _ in range(n):
        _id, qw, qx, qy, qz, tx, ty, tz, _c = struct.unpack("<idddddddi", f.read(64)); name = b""
        while (ch := f.read(1)) != b"\0": name += ch
        npt, = struct.unpack("<Q", f.read(8)); f.seek(24 * npt, 1)
        w, x, y, z = np.array([qw, qx, qy, qz]) / np.linalg.norm([qw, qx, qy, qz])
        R = np.array([[1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)], [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)], [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)]])
        pose[name.decode()] = (R.T, -R.T @ np.array([tx, ty, tz]))      # camera-to-world, centre
cap = cv2.VideoCapture(video); j = -1; fo = open(out, "w"); good = 0; tried = 0
while True:
    ok, fr = cap.read(); j += 1
    if not ok: break
    if j % EVERY: continue
    name = f"{prefix}/{j * stride:05d}.jpg"
    if name not in pose: continue
    c2w, c = pose[name]; Rp = Rot.from_matrix(RS @ c2w @ Rz); pp = S * RS @ c + tS
    q = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), (RW, RH), interpolation=cv2.INTER_AREA)
    views = [(Rp * Rot.from_rotvec([0, math.radians(yw), 0]), pp) for yw in (0, -25, 25)]
    rgb, dep, al, vm = render(views); r = solve(q, rgb, dep, al, vm); tried += 1
    if r is not None:
        for _ in range(2):                                  # match again from the pose it found
            rgb, dep, al, vm = render([(Rot.from_quat(r[0]["quat"]), np.array(r[0]["pos"]))]); r2 = solve(q, rgb, dep, al, vm)
            if r2 is None or r2[0]["inliers"] <= r[0]["inliers"]: break
            r = r2
    d = float(np.linalg.norm(np.array(r[0]["pos"]) - pp)) if r else None
    row = dict(i=j * stride, pred=pp.round(3).tolist(), **({"pos": r[0]["pos"], "inl": r[0]["inliers"], "dpos": round(d, 2)} if r else {}))
    fo.write(json.dumps(row) + "\n"); fo.flush(); good += bool(r and r[0]["inliers"] >= 100)
    print(f"#{j * stride} inl {r[0]['inliers'] if r else None} dpos {d if d is None else round(d, 2)}", flush=True)
print(f"done: {good}/{tried} with >= 100 inliers")
