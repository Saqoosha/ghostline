"""Re-solve a final's frames on a 3DGS with the day geometry, by rendering and matching (cpr_gap_lib.py): each frame is
rendered at its night pose (night_anchor.py), matched with MASt3R and solved by PnP from the render's depth. A frame
is kept when the answer agrees with the night pose (MIN_INL inliers, within MOVE_M and MOVE_DEG of it) and left
ok: false otherwise, for cpr_gap.py (ALL=1) to track through from both sides. The night map folds the course's left
hook onto the right arc: a frame put on the wrong one renders a place it does not see (6-30 inliers), where the right
place gives hundreds to thousands.
Each feed's camera is the night map's (SIMPLE_RADIAL_FISHEYE, written as OPENCV_FISHEYE): the loc json's is the day
localizer's, whose focal length diverged on most night feeds (8e8 px), and the frames are the night map's full-size ones.
usage (rt4090, ~/mastenv): night_refine.py <scene.ply> <top_frame.json> <fit.json> <night model> <loc dir> <images root>
<masks root> <out dir> <feed> ...
writes <out dir>/<feed>.json ("how": "render" for a kept frame; every frame keeps its night pose under "night") and
<out dir>/frames/{images,masks}/<feed> (links), the frames dir cpr_gap.py takes.
env: MIN_INL (60), MOVE_M (3), MOVE_DEG (15), ONLY_BOX (page world x0,x1,z0,z1, e.g. -141,-101,-63,-19: match only the frames
there, the rest stay unconfirmed night poses, which night_keep.py keeps outside its box anyway; a fifth of the work)
Needs ninja and nvcc on PATH the first time (gsplat builds its CUDA part): PATH=~/mastenv/bin:/usr/local/cuda/bin:$PATH"""
import json, os, sys, time
import numpy as np, pycolmap
from scipy.spatial.transform import Rotation as Rot
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cpr_gap_lib import Scene, Matcher, fisheye_to_pinhole

ply, frame_json, fit_json, model, loc_dir, img_root, msk_root, out_dir = sys.argv[1:9]; feeds = sys.argv[9:]
MIN_INL, MOVE_M, MOVE_DEG = int(os.environ.get("MIN_INL", 60)), float(os.environ.get("MOVE_M", 3)), float(os.environ.get("MOVE_DEG", 15))
BOX = [float(v) for v in os.environ["ONLY_BOX"].split(",")] if os.environ.get("ONLY_BOX") else None
scene = Scene(ply, frame_json, fit_json)
match = Matcher(scene)
rec = pycolmap.Reconstruction(model)
for d in ("images", "masks"): os.makedirs(f"{out_dir}/frames/{d}", exist_ok=True)
for feed in feeds:
    J = json.load(open(f"{loc_dir}/{feed}.json"))
    cam = next((rec.cameras[im.camera_id] for im in rec.images.values() if im.name.startswith(feed + "/")), None)
    if cam is not None:
        f, cx, cy, k = (float(v) for v in cam.params)
        camera = {"model": "OPENCV_FISHEYE", "width": cam.width, "height": cam.height, "params": [f, f, cx, cy, k, 0.0, 0.0, 0.0], "from": "night map"}
    else:                                             # a feed the map has no frame of: the loc json's camera (full size)
        camera = J["camera"]
    for d, root in (("images", img_root), ("masks", msk_root)):
        link = f"{out_dir}/frames/{d}/{feed}"
        if not os.path.islink(link): os.symlink(os.path.abspath(f"{root}/{feed}"), link)
    load = fisheye_to_pinhole(camera, f"{img_root}/{feed}", f"{msk_root}/{feed}")
    J["camera"] = camera
    t0, kept, tried = time.time(), 0, 0
    for n, fr in enumerate(J["frames"]):
        if not fr["ok"]: continue
        tried += 1
        night = {k: fr[k] for k in ("center", "qvec", "inliers")}
        if BOX:
            g = scene.metric(np.array(fr["center"]))[0]
            if not (BOX[0] < g[0] < BOX[1] and BOX[2] < -g[1] < BOX[3]):
                J["frames"][n] = {"t_ms": fr["t_ms"], "ok": False, "inliers": 0, "how": "night-unchecked", "night": night}
                continue
        w, x, y, z = fr["qvec"]; R = Rot.from_quat([x, y, z, w]).inv(); c = np.array(fr["center"])
        q, valid = load(fr["t_ms"])
        r = match.solve(q, valid, R, c)
        move = lambda r: (np.linalg.norm(r[1] - c) * scene.U, np.degrees((r[0].inv() * R).magnitude()))
        if r is not None and r[2] >= MIN_INL and max(move(r)[0] / 0.3, move(r)[1] / 3) > 1:
            r2 = match.solve(q, valid, r[0], r[1])        # moved: a render from the pose found matches more
            if r2 is not None and r2[2] > r[2]: r = r2
        if r is not None:
            dm, dd = move(r)
        if r is None or r[2] < MIN_INL or dm > MOVE_M or dd > MOVE_DEG:
            J["frames"][n] = {"t_ms": fr["t_ms"], "ok": False, "inliers": 0 if r is None else r[2], "how": "night-off", "night": night}
        else:
            q = r[0].inv().as_quat()                  # cam_from_world, x y z w -> w x y z as localize.py writes
            J["frames"][n] = {"t_ms": fr["t_ms"], "ok": True, "inliers": r[2], "center": np.round(r[1], 5).tolist(),
                              "qvec": [float(q[3]), float(q[0]), float(q[1]), float(q[2])], "how": "render", "night": night}
            kept += 1
        if tried % 200 == 0:
            print(f"  {feed}: {kept} of {tried} kept, {(time.time() - t0) / tried:.2f} s a frame", flush=True)
    J["night_refine"] = {"scene": ply, "kept": kept, "tried": tried}
    json.dump(J, open(f"{out_dir}/{feed}.json", "w"))
    print(f"{feed}: {kept} of {tried} kept ({time.time() - t0:.0f} s)", flush=True)
