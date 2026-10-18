"""Where is this phone? One camera frame in, its pose on the scan out - the live tracker's relocalization (rt_track.py) as an
HTTP service, for the AR overlay (ios/GhostlineAR). Per request: XFeat on the frame, DINOv2 retrieval over the map's keyframes,
LighterGlue against the RTOPK best, PnP on each and the best kept; then up to REFINE times: the 3DGS rendered at that pose with
the frame's own intrinsics (rt_render.py), matched and solved again. The map's keyframes were rendered at the drone's 100 deg; a
phone sees ~65 deg, and a render through its own lens is the closer picture.
usage (rt4090, mastenv, ~/xfeat): rt_vps.py map.npz scene.ply
  POST /locate?fx=&fy=&cx=&cy=[&prior=x,y,z,qx,qy,qz,qw]   body: a JPEG (the ARKit capturedImage, any size of the map's aspect);
                                 the intrinsics are the sent image's. prior: where the client predicts this camera is (its ARKit
                                 pose through its last answers) - matched against a render there, no retrieval ("track"); an
                                 answer more than PLAUS_M / PLAUS_DEG from it, or too few inliers, falls back to "reloc".
                                 -> 200 {"ok", "pos", "quat" (x y z w, world from camera, x right / y down / z forward), "inliers",
                                 "how", "steps", "ms"[, "track"]} or {"ok": false, "inliers", ...}; 400 / 413 bad request, 500 error
  GET  /points?n=300000          the scan's splat centres (opacity >= 0.5) as float32 x, y, z + uint8 r, g, b, a per point
  GET  /occluders?n=300000       splats as discs (float32 x, y, z, radius = 2 x the geometric-mean sigma), opacity >= 0.6 and
                                 radius <= 0.3 m, the largest opacity x radius first: the phone draws them into depth only, so
                                 the flight hides behind the room's pillars and furniture
  GET  /splats?n=600000          the n splats with the most opacity x size, as a .splat file (antimatter15's: position, scale,
                                 rgba with opacity, rotation w x y z as uint8) for the phone to draw
  GET  /flight, /dvr             with FLIGHT: a recorded flight to replay over the room - its poses ([t, x, y, z, qx, qy, qz, qw]
                                 per frame, world from camera, COLMAP axes) and its DVR video (the whole mp4, cut to the 4:3
                                 picture; the phone plays it from a file). /flight also carries the viewer's index.json "room"
                                 (top view framing, ceiling cut) and lens.json's "source_fisheye" (the DVR's lens, for the FPV view)
env: PORT (8790), RTOPK (8), RMIN (40), REFINE (2), RNEAR (0.1 m: the office map's near), TOPK (2048), PLAUS_M (0.5), PLAUS_DEG (10),
     LOG (dir: every request's JPEG and answer kept there; unset = none), FLIGHT (dir with poses60.json, dvr.mp4, optional
     index.json and lens.json = the flight's dvr_pinhole.mp4.json), XFEAT_DIR (~/xfeat). No authentication: serve it on the tailnet / LAN only."""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1"); os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(4096 * 3072))   # a small JPEG claiming a huge size is refused before it is decoded
import json, sys, time, threading, traceback, itertools, http.server, urllib.parse, numpy as np, torch, cv2, poselib
sys.path.insert(0, os.path.expanduser(os.environ.get("XFEAT_DIR", "~/xfeat"))); from modules.xfeat import XFeat
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scipy.spatial.transform import Rotation as Rot
from plyfile import PlyData
E = lambda k, d: float(os.environ.get(k, d)); dev = "cuda"
PORT, RTOPK, RMIN, REFINE, RNEAR, TOPK = int(E("PORT", 8790)), int(E("RTOPK", 8)), int(E("RMIN", 40)), int(E("REFINE", 2)), E("RNEAR", 0.1), int(E("TOPK", 2048))
PLAUS_M, PLAUS_DEG = E("PLAUS_M", 0.5), E("PLAUS_DEG", 10)
LOG, FLIGHT = os.environ.get("LOG"), os.environ.get("FLIGHT")
MAXBODY = 8 << 20                                   # a 960 x 720 JPEG is ~100 kB
T = lambda a: torch.tensor(a, dtype=torch.float32, device=dev)
map_path, ply_path = sys.argv[1:3]
with np.load(map_path) as m:                        # the parts of rt_track.load_map that relocalizing uses
    M = dict(kp=T(m["kp"]), desc=torch.tensor(m["desc"], device=dev), X=m["X"], n=m["n"], g=T(m["g"]))
    RW, RH = [int(x) for x in m["size"]]
print(f"map {len(M['n'])} keyframes at {RW}x{RH}", flush=True)
xfeat = XFeat(top_k=TOPK)
from modules.lighterglue import LighterGlue; LighterGlue.default_conf_xfeat["mp"] = False; LighterGlue.default_conf_xfeat["width_confidence"] = -1
lg = LighterGlue().eval()
dino = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14").to(dev).eval()
MEAN, STD = T([0.485, 0.456, 0.406])[None, :, None, None], T([0.229, 0.224, 0.225])[None, :, None, None]
SIZE = T([[RW, RH]])
def detect(img):                                    # as rt_track.detect: [B, 3, H, W] -> kp [B, TOPK, 2], desc [B, TOPK, 64], score (-1 = none)
    with torch.no_grad(): fs = xfeat.detectAndCompute(img, top_k=TOPK)
    kp, d, sc = torch.zeros(len(fs), TOPK, 2, device=dev), torch.zeros(len(fs), TOPK, 64, device=dev), torch.full((len(fs), TOPK), -1.0, device=dev)
    for b, f in enumerate(fs): n = len(f["keypoints"]); kp[b, :n], d[b, :n], sc[b, :n] = f["keypoints"], f["descriptors"], f["scores"]
    return kp, d, sc
from rt_render import Scene
scene = Scene(ply_path, RW, RH, RNEAR, TOPK, detect)
scene.views([(Rot.identity(), np.zeros(3))], [np.diag([RW / 2, RW / 2, 1.0])])   # compile the kernels before the first request

def match(q, refs):                                 # q (kp, desc, ok) of the frame; refs [(kp, desc, X, n)] -> per ref (uv on the frame, X)
    B = len(refs); kp0, d0, ok0 = q[0][None].expand(B, -1, -1), (q[1] * q[2][:, None])[None].expand(B, -1, -1), q[2]
    kp1, d1 = torch.stack([r[0] for r in refs]), torch.stack([r[1].float() for r in refs])
    with torch.no_grad():
        m0 = lg.net({"image0": {"keypoints": kp0, "descriptors": d0, "image_size": SIZE.expand(B, 2)},
                     "image1": {"keypoints": kp1, "descriptors": d1, "image_size": SIZE.expand(B, 2)}})["matches0"].cpu().numpy()
    kp0, ok0 = q[0].cpu().numpy(), ok0.cpu().numpy(); out = []
    for b, r in enumerate(refs):
        i = np.nonzero((m0[b] >= 0) & ok0)[0]; j = m0[b][i]; keep = j < r[3]
        out.append((kp0[i[keep]], r[2][j[keep]]))
    return out
def pnp(K, uv, X):                                  # PoseLib, then LM on the inliers, as rt_track.pnp (more trials: one frame, not 60 Hz)
    if len(uv) < 12: return None
    pose, r = poselib.estimate_absolute_pose(uv.astype(np.float64), X.astype(np.float64), {"model": "PINHOLE", "width": RW, "height": RH, "params": [K[0, 0], K[1, 1], K[0, 2], K[1, 2]]},
                                             {"max_reproj_error": 3.0, "max_iterations": 1000, "min_iterations": 10, "success_prob": 0.999}, {})
    inl = np.flatnonzero(r["inliers"])
    if len(inl) < 12: return None
    rv, tv = cv2.solvePnPRefineLM(X[inl].astype(np.float64), uv[inl].astype(np.float64), K, None, cv2.Rodrigues(pose.R)[0], pose.t.reshape(3, 1).copy())
    Rw = cv2.Rodrigues(rv)[0]; return Rot.from_matrix(Rw.T), -Rw.T @ tv[:, 0], len(inl)

class BadRequest(ValueError): pass
lock = threading.Lock()
def refine(q, K, pose, n):                          # the scan rendered at pose through K, matched and solved, up to n times -> (best answer or None, inliers per try)
    best, steps = None, []
    for _ in range(n):
        v = scene.views([pose], [K])[0]; r = pnp(K, *match(q, [(v["kp"], v["desc"], v["X"], v["n"])])[0])
        steps.append(r[2] if r else 0)
        if not r or r[2] < RMIN: break
        best = r; pose = (r[0], r[1])
    return best, steps
def near(r, pose):                                  # an answer close enough to the client's prediction to trust (a repeated structure can match far off)
    return np.linalg.norm(r[1] - pose[1]) <= PLAUS_M and np.degrees((r[0].inv() * pose[0]).magnitude()) <= PLAUS_DEG
def locate(jpeg, fx, fy, cx, cy, prior=None):
    t0 = time.perf_counter(); ms = lambda: (time.perf_counter() - t0) * 1000
    if not all(np.isfinite([fx, fy, cx, cy])) or fx <= 0 or fy <= 0: raise BadRequest("intrinsics must be finite, focal > 0")
    if prior is not None:
        prior = np.atleast_1d(np.asarray(prior, float))
        if prior.shape != (7,) or not np.all(np.isfinite(prior)) or np.linalg.norm(prior[3:]) < 1e-6: raise BadRequest("prior must be 7 finite numbers, x y z qx qy qz qw")
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None: raise BadRequest(f"not a JPEG ({len(jpeg)} bytes)")
    h, w = img.shape[:2]; s = RW / w
    if abs(h * s - RH) >= 2: raise BadRequest(f"{w}x{h} is not the map's aspect ({RW}x{RH})")
    K = np.array([[fx * s, 0, cx * s], [0, fy * s, cy * s], [0, 0, 1]])
    x = T(cv2.cvtColor(cv2.resize(img, (RW, RH), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)).permute(2, 0, 1)[None] / 255
    with lock, torch.no_grad():
        kp, d, sc = detect(x); q = (kp[0], d[0], sc[0] > 0)
        tried = None
        if prior is not None:                       # the client's prediction: no retrieval; relocalize only if it fails
            pose = (Rot.from_quat(prior[3:]), prior[:3])
            r, tried = refine(q, K, pose, max(1, REFINE))
            if r and near(r, pose): return dict(ok=True, pos=r[1].tolist(), quat=r[0].as_quat().tolist(), inliers=r[2], how="track", steps=tried, ms=ms())
        g = torch.nn.functional.normalize(dino((torch.nn.functional.interpolate(x, (168, 224), mode="area") - MEAN) / STD), dim=1)[0]
        ks = torch.topk(M["g"] @ g, min(RTOPK, len(M["n"]))).indices.tolist()
        best = None
        for k, (uv, X) in zip(ks, match(q, [(M["kp"][k], M["desc"][k], M["X"][k], M["n"][k]) for k in ks])):
            r = pnp(K, uv, X)
            if r and (best is None or r[2] > best[2]): best = r
        extra = {} if tried is None else dict(track=tried)   # why the prior was not used
        if best is None or best[2] < RMIN: return dict(ok=False, inliers=best[2] if best else 0, ms=ms(), **extra)
        r, steps = refine(q, K, (best[0], best[1]), REFINE)   # the scan through the phone's own lens at the answer
    steps = [best[2]] + steps                       # the keyframe match's inliers, then each render's
    if r: best = r
    return dict(ok=True, pos=best[1].tolist(), quat=best[0].as_quat().tolist(), inliers=best[2], how="render" if r else "reloc",
                steps=steps, ms=ms(), **extra)

vlock, V = threading.Lock(), None
def vertices():                                     # the ply's vertex table, read once (only the data endpoints need it)
    global V
    with vlock:
        if V is None: V = PlyData.read(ply_path)["vertex"].data
    return V
def cached(cache, n, make):                         # n clamped to the scan's size; a few sizes kept
    n = max(1, min(int(n), len(vertices())))
    with vlock:
        if n in cache: return cache[n]
    b = make(vertices(), n)
    with vlock:
        cache[n] = b
        while len(cache) > 4: cache.pop(next(iter(cache)))
    return b

def points(v, n):                                   # the splat centres, thinned at random to n, with their base colour
    v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.5]
    v = v[np.random.default_rng(0).permutation(len(v))[:n]]
    rgb = np.clip(0.5 + 0.28209479 * np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], 0, 1) * 255
    a = np.zeros(len(v), [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1"), ("a", "u1")])
    a["x"], a["y"], a["z"], a["r"], a["g"], a["b"], a["a"] = v["x"], v["y"], v["z"], rgb[:, 0], rgb[:, 1], rgb[:, 2], 255
    return a.tobytes()

def occluders(v, n):                                # see /occluders above
    op = 1 / (1 + np.exp(-v["opacity"])); r = 2 * np.cbrt(np.exp(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]]).prod(1))
    k = np.flatnonzero((op >= 0.6) & (r <= 0.3)); k = k[np.argsort(-(op[k] * r[k]))[:n]]
    return np.c_[v["x"][k], v["y"][k], v["z"][k], r[k]].astype("<f4").tobytes()

def splats(v, n):                                   # the n splats with the most opacity x size, as a .splat file (32 bytes each, SH0 only)
    op = 1 / (1 + np.exp(-v["opacity"])); sc = np.exp(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]])
    k = np.argsort(-(op * np.cbrt(sc.prod(1))))[:n]; v, op, sc = v[k], op[k], sc[k]
    q = np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]]; q /= np.linalg.norm(q, axis=1, keepdims=True)   # w, x, y, z
    a = np.zeros(len(v), [("p", "<f4", 3), ("s", "<f4", 3), ("c", "u1", 4), ("q", "u1", 4)])
    a["p"], a["s"] = np.c_[v["x"], v["y"], v["z"]], sc
    a["c"] = np.clip(np.c_[0.5 + 0.28209479 * np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], op] * 255, 0, 255)
    a["q"] = np.clip(q * 128 + 128, 0, 255)
    return a.tobytes()
caches = dict(points={}, occluders={}, splats={})

seq = itertools.count()
class H(http.server.BaseHTTPRequestHandler):
    timeout = 10                                    # a client that stops sending mid-request does not hold a thread
    def reply(self, code, body, kind):
        self.send_response(code); self.send_header("Content-Type", kind); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def fail(self, code, e):
        if code >= 500: traceback.print_exc()
        self.reply(code, json.dumps(dict(ok=False, error=f"{type(e).__name__}: {e}")).encode(), "application/json")
    def do_GET(self):
        try:
            u = urllib.parse.urlparse(self.path); a = dict(urllib.parse.parse_qsl(u.query))
            make = dict(points=(points, 300000), occluders=(occluders, 300000), splats=(splats, 600000)).get(u.path.strip("/"))
            if make:
                try: n = int(a.get("n", make[1]))
                except ValueError as e: return self.fail(400, e)
                return self.reply(200, cached(caches[u.path.strip("/")], n, make[0]), "application/octet-stream")
            if u.path == "/flight" and FLIGHT:      # the recorded flight, slimmed to [t, x, y, z, qx, qy, qz, qw] per frame
                with open(os.path.join(FLIGHT, "poses60.json")) as f: p = json.load(f)
                def optional(name, key):            # the viewer's index.json "room" (top view, ceiling); the camera json's "source_fisheye" (FPV)
                    path = os.path.join(FLIGHT, name)
                    if not os.path.exists(path): return None
                    try:
                        with open(path) as f: return json.load(f).get(key)
                    except ValueError as e: print(f"{path}: {e}, no {key}", flush=True); return None
                return self.reply(200, json.dumps(dict(fps=p.get("fps"), path=[[q["t"], *q["pos"], *q["quat"]] for q in p["poses"] if q],
                                                       room=optional("index.json", "room"), lens=optional("lens.json", "source_fisheye"))).encode(), "application/json")
            if u.path == "/dvr" and FLIGHT:
                with open(os.path.join(FLIGHT, "dvr.mp4"), "rb") as f: return self.reply(200, f.read(), "video/mp4")
            self.reply(404, b"", "text/plain")
        except Exception as e: self.fail(500, e)    # a bad n is answered above; anything else here is the server's
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/locate": return self.reply(404, b"", "text/plain")
        try:                                        # the request itself: 400
            # extra fields (ar: the client's own pose) are only logged
            a = {k: [float(x) for x in v.split(",")] if "," in v else float(v) for k, v in urllib.parse.parse_qsl(u.query)}
            fx, fy, cx, cy = a["fx"], a["fy"], a["cx"], a["cy"]
            n = int(self.headers.get("Content-Length") or -1)
        except (ValueError, KeyError) as e: return self.fail(400, e)
        if not 0 < n <= MAXBODY: return self.fail(413, ValueError(f"body of {n} bytes (1..{MAXBODY})"))
        try:
            jpeg = self.rfile.read(n)
            r = locate(jpeg, fx, fy, cx, cy, a.get("prior"))
        except BadRequest as e: return self.fail(400, e)
        except Exception as e: return self.fail(500, e)   # the server's own failure: traceback in the journal
        self.reply(200, json.dumps(r).encode(), "application/json")
        print(json.dumps(r), flush=True)
        if LOG:                                     # after the reply: a full disk loses the log, not the answer
            try:
                os.makedirs(LOG, exist_ok=True); stem = os.path.join(LOG, time.strftime("%Y%m%d-%H%M%S") + f"-{next(seq):06d}")
                with open(stem + ".jpg", "wb") as f: f.write(jpeg)
                with open(stem + ".json", "w") as f: json.dump(dict(r, query=a), f)
            except OSError as e: print(f"log: {e}", flush=True)
    def log_request(self, *a): pass                 # no access log; errors still go to stderr
print(f"VPS-READY on :{PORT}", flush=True)
http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
