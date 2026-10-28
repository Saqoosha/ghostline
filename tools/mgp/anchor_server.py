"""The anchor-clicking page: pair points in a footage frame with map 3D points (keypoints of a map image), and see the
frame's pose solved from them at once. Serves tools/mgp/anchor.html; saves <work>/anchors.json.
usage: anchor_server.py <map model> <map image root> <footage model (its camera)> <footage image dir> <anchor_in dir> <work> [port=8765]"""
import json, os, sys, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote
import numpy as np
import pycolmap as p

map_dir, root, foot_dir, fdir, adir, work = sys.argv[1:7]
port = int(sys.argv[7]) if len(sys.argv) > 7 else 8765
HERE = os.path.dirname(os.path.abspath(__file__))
UNIT = 7.37
ren = {"r01_": "r1034_"}
M = p.Reconstruction(map_dir)
F = p.Reconstruction(foot_dir)
fcam = max(F.cameras.values(), key=lambda c: (c.width, sum(im.camera_id == c.camera_id for im in F.images.values())))   # the footage is the widest camera in the model
byname = {im.name: im for im in M.images.values()}
P = np.array([q.xyz for q in M.points3D.values()])
mu = np.median(P, 0); U = np.linalg.svd(P[::20] - mu, full_matrices=False)[2]
top = lambda X: ((np.atleast_2d(X) - mu) @ U[:2].T * UNIT)
cams = []
for im in M.images.values():
    c = im.projection_center(); f = im.cam_from_world().rotation.matrix()[2]       # the optical axis in world
    xy = top(c)[0]; d = (f @ U[:2].T)
    cams.append([im.name.replace("/", "__"), round(float(xy[0]), 2), round(float(xy[1]), 2), round(float(np.arctan2(d[1], d[0])), 3)])
cloud = top(P[np.random.default_rng(0).choice(len(P), min(len(P), 60000), replace=False)]).round(1).tolist()
AIN = json.load(open(f"{adir}/anchors_in.json"))
nfly = len([f for f in os.listdir(fdir) if f.endswith(".jpg")])
os.makedirs(work, exist_ok=True)
SAVE = f"{work}/anchors.json"
lock = threading.Lock()


fcam0 = fcam                                             # the guess from the footage's registration (its focal was ~10% long)


def calibrate(saved):
    """one footage camera from every anchor: focal (fx/fy ratio kept) and the 4 fisheye terms, shared, with each anchor's
    pose, robust to wrong pairs (soft-L1 at 6 px); a frame takes part with 8+ pairs over 3+ quarters. Returns the camera,
    or the guess when fewer than 2 frames qualify."""
    from scipy.optimize import least_squares
    from scipy.spatial.transform import Rotation as Rot
    A = []
    for a in saved.values():
        if len(a.get("pairs", [])) < 8:                  # only well-spread frames: a few weak ones made the camera swing per save
            continue
        P2 = np.array([q[:2] for q in a["pairs"]], float); P3 = np.array([M.points3D[int(q[2])].xyz for q in a["pairs"]])
        if len({(int(x >= fcam0.width / 2), int(y >= fcam0.height / 2)) for x, y in P2}) < 3:
            continue
        o = p.AbsolutePoseEstimationOptions(); o.ransac.max_error = 12.0
        r = p.estimate_and_refine_absolute_pose(P2, P3, fcam0, o)
        if r is not None:
            T = r["cam_from_world"]; A.append((P2, P3, Rot.from_matrix(T.rotation.matrix()).as_rotvec(), T.translation.copy()))
    if len(A) < 2:
        return fcam0
    k0 = np.array(fcam0.params[4:8]); ratio = fcam0.params[1] / fcam0.params[0]

    def cam_of(x):
        return p.Camera(camera_id=fcam0.camera_id, model=fcam0.model, width=fcam0.width, height=fcam0.height,
                        params=[x[0], x[0] * ratio, fcam0.params[2], fcam0.params[3], *x[1:5]])

    def res(x):
        c = cam_of(x); out = []
        for i, (P2, P3, _, _) in enumerate(A):
            rv, t = x[5 + 6 * i:8 + 6 * i], x[8 + 6 * i:11 + 6 * i]
            Xc = (Rot.from_rotvec(rv).as_matrix() @ P3.T).T + t
            uv = c.img_from_cam(np.where(Xc[:, 2:3] > 1e-3, Xc, np.c_[Xc[:, :2], np.full(len(Xc), 1e-3)]))
            out.append((uv - P2).ravel())
        out.append((x[1:5] - k0) * 20)                  # the fisheye terms stay near the guess unless the anchors insist
        return np.concatenate(out)

    x0 = np.concatenate([[fcam0.params[0]], k0] + [np.r_[rv, t] for _, _, rv, t in A])
    s = least_squares(res, x0, loss="soft_l1", f_scale=6.0, max_nfev=200)
    c = cam_of(s.x)
    json.dump({"model": c.model.name, "width": c.width, "height": c.height, "params": [float(x) for x in c.params], "anchors": len(A)},
              open(f"{work}/camera.json", "w"), indent=1)    # for anchor_register.py
    print(f"calibrated from {len(A)} anchors: f {fcam0.params[0]:.1f} -> {s.x[0]:.1f}, k {np.round(k0, 3)} -> {np.round(s.x[1:5], 3)}", flush=True)
    return c


STRONG = 8                                               # a map point seen in this many images (drawn big on the page)


STRONG_FIRST = False                                     # tried: Q754's strong points disagree among themselves by 40+ px
                                                         # (the map is not rigid there), and leading with them doubled the wobble


def fit(P2, P3, strong):
    """RANSAC over all pairs (with STRONG_FIRST: the strong points first, then every pair that agrees with them)."""
    o = p.AbsolutePoseEstimationOptions(); o.ransac.max_error = 12.0
    if STRONG_FIRST and strong.sum() >= 4:
        r = p.estimate_and_refine_absolute_pose(P2[strong], P3[strong], fcam, o)
        if r is not None:
            T = r["cam_from_world"]
            e = np.linalg.norm(fcam.img_from_cam((T.rotation.matrix() @ P3.T).T + T.translation) - P2, axis=1)
            use = strong | (e <= 8)
            r2 = p.estimate_and_refine_absolute_pose(P2[use], P3[use], fcam, o)
            return (r2 or r)["cam_from_world"]
    r = p.estimate_and_refine_absolute_pose(P2, P3, fcam, o)
    return r["cam_from_world"] if r is not None else None


def solve(a):
    """the footage frame's pose from its clicked pairs: PnP with the footage camera, residuals in px, centre in top view."""
    pts2 = np.array([[q[0], q[1]] for q in a["pairs"]], float)
    pts3 = np.array([M.points3D[int(q[2])].xyz for q in a["pairs"]], float)
    strong = np.array([M.points3D[int(q[2])].track.length() >= STRONG for q in a["pairs"]])
    if len(pts2) < 4:
        return {"ok": False, "why": f"{len(pts2)} pairs, need 4"}
    T = fit(pts2, pts3, strong)
    if T is None:
        return {"ok": False, "why": "no pose"}
    proj = fcam.img_from_cam((T.rotation.matrix() @ pts3.T).T + T.translation)
    err = np.linalg.norm(proj - pts2, axis=1)
    c = -T.rotation.matrix().T @ T.translation
    allp = []                                            # the map image's other points drawn back: they land on their spots when the pose is right
    im = byname.get(a["map"].replace("__", "/", 1))
    if im is not None:
        X = np.array([M.points3D[q.point3D_id].xyz for q in im.points2D if q.has_point3D()])
        Xc = (T.rotation.matrix() @ X.T).T + T.translation
        Xc = Xc[Xc[:, 2] > 0.05]
        if len(Xc):
            uv = fcam.img_from_cam(Xc)
            ok = (uv[:, 0] >= 0) & (uv[:, 0] < fcam.width) & (uv[:, 1] >= 0) & (uv[:, 1] < fcam.height)
            allp = uv[ok].round(1).tolist()
    # how far the centre moves when one pair is left out: a pose resting on one small object swings by metres
    shift = []
    if len(pts2) >= 6:
        for k in range(len(pts2)):
            keep = np.arange(len(pts2)) != k
            T2 = fit(pts2[keep], pts3[keep], strong[keep])
            if T2 is not None:
                shift.append(float(np.linalg.norm(-T2.rotation.matrix().T @ T2.translation - c)) * UNIT)
    W, Hh = fcam.width, fcam.height
    quads = len({(int(x >= W / 2), int(y >= Hh / 2)) for x, y in pts2})
    return {"ok": True, "inliers": int((err <= 8).sum()), "strong": strong.astype(int).tolist(), "err": err.round(1).tolist(), "top": top(c)[0].round(2).tolist(),
            "wobble_m": round(max(shift), 2) if shift else None, "quads": quads,
            "height_m": round(float((c - mu) @ U[2] * UNIT), 2), "proj": proj.round(1).tolist(), "all": allp}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code); self.send_header("content-type", ctype); self.send_header("cache-control", "no-cache")
        self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def file(self, path, ctype):
        if not os.path.isfile(path):
            return self.send(b"not found", "text/plain", 404)
        self.send(open(path, "rb").read(), ctype)

    def do_GET(self):
        u = unquote(self.path.split("?")[0])
        if u == "/":
            return self.file(f"{HERE}/anchor.html", "text/html; charset=utf-8")
        if u == "/data.json":
            saved = json.load(open(SAVE)) if os.path.exists(SAVE) else {}
            return self.send({"frames": AIN["frames"], "nfly": nfly, "cams": cams, "cloud": cloud, "saved": saved,
                              "fly_size": [fcam.width, fcam.height]})
        if u.startswith("/fly/"):
            n = os.path.basename(u)
            return self.file(f"{fdir}/{n}", "image/jpeg")
        if u.startswith("/map/"):
            flat = os.path.basename(u); name = flat.replace("__", "/", 1)
            if name not in byname:                       # only the map's own images: "..__x" would otherwise reach the root's parent
                return self.send(b"not found", "text/plain", 404)
            disk = name
            for a, b in ren.items():
                if disk.startswith(a):
                    disk = b + disk[len(a):]
            return self.file(f"{root}/{disk}", "image/jpeg")
        if u.startswith("/pts/"):
            name = os.path.basename(u).replace("__", "/", 1); im = byname.get(name)
            if im is None:
                return self.send({"pts": []})
            cam = M.cameras[im.camera_id]
            return self.send({"w": cam.width, "h": cam.height,
                              "pts": [[round(float(q.xy[0]), 1), round(float(q.xy[1]), 1), int(q.point3D_id), M.points3D[q.point3D_id].track.length()]
                                      for q in im.points2D if q.has_point3D()]})
        self.send(b"not found", "text/plain", 404)

    def do_POST(self):
        if self.path != "/save":
            return self.send(b"not found", "text/plain", 404)
        global fcam
        a = json.loads(self.rfile.read(int(self.headers["content-length"])))    # {fly, map, pairs: [[fx, fy, pid], ...]}
        with lock:
            saved = json.load(open(SAVE)) if os.path.exists(SAVE) else {}
            if a["pairs"]:
                saved[a["fly"]] = a
            else:
                saved.pop(a["fly"], None)
            fcam = calibrate(saved)                      # every anchor refines the shared camera, so all are solved again
            for k, b in saved.items():
                b["solve"] = solve(b)
            json.dump(saved, open(SAVE + ".tmp", "w"), indent=1); os.replace(SAVE + ".tmp", SAVE)
        self.send(saved[a["fly"]]["solve"] if a["fly"] in saved else solve(a))


if os.path.exists(SAVE):                                 # solved again with this run's camera
    saved = json.load(open(SAVE))
    fcam = calibrate(saved)
    for k, a in saved.items():
        a["solve"] = solve(a)
    json.dump(saved, open(SAVE + ".tmp", "w"), indent=1); os.replace(SAVE + ".tmp", SAVE)
print(f"http://0.0.0.0:{port}/  ({len(M.images)} map images, footage camera {fcam.model.name} {fcam.width}x{fcam.height})", flush=True)
ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
