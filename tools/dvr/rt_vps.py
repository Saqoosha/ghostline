"""Where is this phone? One camera frame in, its pose on the scan out - the live tracker's relocalization (rt_track.py) as an
HTTP service, for the AR overlay (ios/GhostlineAR). Per request: XFeat on the frame, DINOv2 retrieval over the map's keyframes,
LighterGlue against the RTOPK best, PnP on each and the best kept; then REFINE times: the 3DGS rendered at that pose with the
frame's own intrinsics (rt_render.py), matched and solved again. The map's keyframes were rendered at the drone's 100 deg; a
phone sees ~65 deg, and a render through its own lens is the closer picture.
usage (rt4090, mastenv, ~/xfeat): rt_vps.py map.npz scene.ply
  POST /locate?fx=&fy=&cx=&cy=[&prior=x,y,z,qx,qy,qz,qw]   body: a JPEG (the ARKit capturedImage, any size of the map's aspect);
                                 the intrinsics are the sent image's. prior: where the client predicts this camera is (its ARKit
                                 pose through its last answers) - matched against a render there, no retrieval ("track"; falls
                                 back to "reloc"). -> {"ok", "pos", "quat" (world from camera, x right / y down / z forward),
                                 "inliers", "how", "ms"}
  GET  /points?n=300000          the scan's splat centres (opacity >= 0.5) as float32 x, y, z + uint8 r, g, b, a per point
  GET  /occluders?n=300000       the most solid splats as discs (float32 x, y, z, radius): the phone draws them into depth only,
                                 so the flight hides behind the room's pillars and furniture
  GET  /splats?n=600000         the n splats with the most opacity x size, as a .splat file (antimatter15's: position, scale,
                                 rgba with opacity, rotation w x y z as uint8) for the phone to draw
  GET  /flight, /dvr             with FLIGHT: a recorded flight to replay over the room - its poses ([t, x, y, z] per frame)
                                 and its DVR video (the whole mp4; the phone plays it from a file); /flight also carries the
                                 viewer's index.json "room" (if FLIGHT has one), for the ceiling the top view cuts off
env: PORT (8790), RTOPK (8), RMIN (40), REFINE (2), RNEAR (0.1 m: the office map's near), TOPK (2048), LOG (dir: every
     request's JPEG and answer kept there; unset = none), FLIGHT (dir with poses60.json and dvr.mp4)"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1"); os.environ.setdefault("OMP_NUM_THREADS", "1")
import json, sys, time, threading, http.server, urllib.parse, numpy as np, torch, cv2, poselib
sys.path.insert(0, os.path.expanduser(os.environ.get("XFEAT_DIR", "~/xfeat"))); from modules.xfeat import XFeat
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scipy.spatial.transform import Rotation as Rot
from plyfile import PlyData
E = lambda k, d: float(os.environ.get(k, d)); dev = "cuda"
PORT, RTOPK, RMIN, REFINE, RNEAR, TOPK = int(E("PORT", 8790)), int(E("RTOPK", 8)), int(E("RMIN", 40)), int(E("REFINE", 2)), E("RNEAR", 0.1), int(E("TOPK", 2048))
LOG, FLIGHT = os.environ.get("LOG"), os.environ.get("FLIGHT")
T = lambda a: torch.tensor(a, dtype=torch.float32, device=dev)
map_path, ply_path = sys.argv[1:3]
with np.load(map_path) as m:                        # as rt_track.load_map
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
def pnp(K, uv, X):                                  # as rt_track.pnp (PoseLib, then LM on the inliers) -> (Rot world from camera, centre, inliers)
    if len(uv) < 12: return None
    pose, r = poselib.estimate_absolute_pose(uv.astype(np.float64), X.astype(np.float64), {"model": "PINHOLE", "width": RW, "height": RH, "params": [K[0, 0], K[1, 1], K[0, 2], K[1, 2]]},
                                             {"max_reproj_error": 3.0, "max_iterations": 1000, "min_iterations": 10, "success_prob": 0.999}, {})
    inl = np.flatnonzero(r["inliers"])
    if len(inl) < 12: return None
    rv, tv = cv2.solvePnPRefineLM(X[inl].astype(np.float64), uv[inl].astype(np.float64), K, None, cv2.Rodrigues(pose.R)[0], pose.t.reshape(3, 1).copy())
    Rw = cv2.Rodrigues(rv)[0]; return Rot.from_matrix(Rw.T), -Rw.T @ tv[:, 0], len(inl)

lock = threading.Lock()
def track(q, K, prior):                             # the scan rendered at the client's prediction, matched, solved, once more at the answer
    best, steps = (Rot.from_quat(prior[3:]), np.array(prior[:3])), []
    for _ in range(max(1, REFINE)):
        v = scene.views([best[:2]], [K])[0]; r = pnp(K, *match(q, [(v["kp"], v["desc"], v["X"], v["n"])])[0])
        steps.append(r[2] if r else 0)
        if not r or r[2] < RMIN: break
        best = r
    return (best, steps) if len(best) == 3 else (None, steps)
def locate(jpeg, fx, fy, cx, cy, prior=None):
    t0 = time.perf_counter(); img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]; s = RW / w
    assert abs(h * s - RH) < 2, f"{w}x{h} is not the map's aspect ({RW}x{RH})"
    K = np.array([[fx * s, 0, cx * s], [0, fy * s, cy * s], [0, 0, 1]])
    x = T(cv2.cvtColor(cv2.resize(img, (RW, RH), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)).permute(2, 0, 1)[None] / 255
    with lock, torch.no_grad():
        kp, d, sc = detect(x); q = (kp[0], d[0], sc[0] > 0)
        if prior is not None:                       # the client's prediction: no retrieval; relocalize only if it fails
            r, steps = track(q, K, prior)
            if r: return dict(ok=True, pos=r[1].tolist(), quat=r[0].as_quat().tolist(), inliers=r[2], how="track", steps=steps, ms=(time.perf_counter() - t0) * 1000)
        g =torch.nn.functional.normalize(dino((torch.nn.functional.interpolate(x, (168, 224), mode="area") - MEAN) / STD), dim=1)[0]
        ks = torch.topk(M["g"] @ g, RTOPK).indices.tolist()
        best = None
        for k, (uv, X) in zip(ks, match(q, [(M["kp"][k], M["desc"][k], M["X"][k], M["n"][k]) for k in ks])):
            r = pnp(K, uv, X)
            if r and (best is None or r[2] > best[2]): best = r
        if best is None or best[2] < RMIN: return dict(ok=False, inliers=best[2] if best else 0, ms=(time.perf_counter() - t0) * 1000)
        how, steps = "reloc", [best[2]]
        for _ in range(REFINE):                     # the scan through the phone's own lens at the answer
            v = scene.views([(best[0], best[1])], [K])[0]
            r = pnp(K, *match(q, [(v["kp"], v["desc"], v["X"], v["n"])])[0])
            steps.append(r[2] if r else 0)
            if not r or r[2] < RMIN: break
            best, how = r, "render"
    return dict(ok=True, pos=best[1].tolist(), quat=best[0].as_quat().tolist(), inliers=best[2], how=how, steps=steps, ms=(time.perf_counter() - t0) * 1000)

pts = {}
def points(n):                                      # the splat centres, thinned at random to n, with their base colour
    if n not in pts:
        v = PlyData.read(ply_path)["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.5]
        v = v[np.random.default_rng(0).permutation(len(v))[:n]]
        rgb = np.clip(0.5 + 0.28209479 * np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], 0, 1) * 255
        a = np.zeros(len(v), [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1"), ("a", "u1")])
        a["x"], a["y"], a["z"], a["r"], a["g"], a["b"], a["a"] = v["x"], v["y"], v["z"], rgb[:, 0], rgb[:, 1], rgb[:, 2], 255
        pts[n] = a.tobytes()
    return pts[n]

occ = {}
def occluders(n):                                   # the n most solid splats (opacity >= 0.6) as discs, float32 x, y, z, radius (2 sigma, <= 0.3 m)
    if n not in occ:
        v = PlyData.read(ply_path)["vertex"].data
        op = 1 / (1 + np.exp(-v["opacity"])); r = 2 * np.cbrt(np.exp(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]]).prod(1))
        k = np.flatnonzero((op >= 0.6) & (r <= 0.3)); k = k[np.argsort(-(op[k] * r[k]))[:n]]
        occ[n] = np.c_[v["x"][k], v["y"][k], v["z"][k], r[k]].astype("<f4").tobytes()
    return occ[n]

spl = {}
def splats(n):                                      # the n splats with the most opacity x size, as a .splat file (32 bytes each, SH0 only)
    if n not in spl:
        v = PlyData.read(ply_path)["vertex"].data
        op = 1 / (1 + np.exp(-v["opacity"])); sc = np.exp(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]])
        v, op, sc = (a[np.argsort(-(op * np.cbrt(sc.prod(1))))[:n]] for a in (v, op, sc))
        q = np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]]; q /= np.linalg.norm(q, axis=1, keepdims=True)   # w, x, y, z
        a = np.zeros(len(v), [("p", "<f4", 3), ("s", "<f4", 3), ("c", "u1", 4), ("q", "u1", 4)])
        a["p"], a["s"] = np.c_[v["x"], v["y"], v["z"]], sc
        a["c"] = np.clip(np.c_[0.5 + 0.28209479 * np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], op] * 255, 0, 255)
        a["q"] = np.clip(q * 128 + 128, 0, 255)
        spl[n] = a.tobytes()
    return spl[n]

class H(http.server.BaseHTTPRequestHandler):
    def reply(self, code, body, kind):
        self.send_response(code); self.send_header("Content-Type", kind); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); a = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/points": self.reply(200, points(int(a.get("n", 300000))), "application/octet-stream")
        elif u.path == "/occluders": self.reply(200, occluders(int(a.get("n", 300000))), "application/octet-stream")
        elif u.path == "/splats": self.reply(200, splats(int(a.get("n", 600000))), "application/octet-stream")
        elif u.path == "/flight" and FLIGHT:        # the recorded flight, slimmed to [t, x, y, z] per frame
            p = json.load(open(os.path.join(FLIGHT, "poses60.json")))
            ix = os.path.join(FLIGHT, "index.json")   # the viewer's: its "room" tells the phone where the ceiling starts
            room = json.load(open(ix)).get("room") if os.path.exists(ix) else None
            self.reply(200, json.dumps(dict(fps=p.get("fps"), path=[[q["t"], *q["pos"]] for q in p["poses"]], room=room)).encode(), "application/json")
        elif u.path == "/dvr" and FLIGHT: self.reply(200, open(os.path.join(FLIGHT, "dvr.mp4"), "rb").read(), "video/mp4")
        else: self.reply(404, b"", "text/plain")
    def do_POST(self):
        u = urllib.parse.urlparse(self.path); a = {k: [float(x) for x in v.split(",")] if "," in v else float(v) for k, v in urllib.parse.parse_qsl(u.query)}
        if u.path != "/locate": return self.reply(404, b"", "text/plain")   # extra fields (ar: the client's own pose) are only logged
        jpeg = self.rfile.read(int(self.headers["Content-Length"]))
        try: r = locate(jpeg, a["fx"], a["fy"], a["cx"], a["cy"], a.get("prior"))
        except Exception as e: r = dict(ok=False, error=f"{type(e).__name__}: {e}")
        print(json.dumps(r), flush=True)
        if LOG:
            os.makedirs(LOG, exist_ok=True); stem = os.path.join(LOG, time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}")
            open(stem + ".jpg", "wb").write(jpeg); json.dump(dict(r, query=a), open(stem + ".json", "w"))
        self.reply(200, json.dumps(r).encode(), "application/json")
    def log_message(self, *a): pass
print(f"VPS-READY on :{PORT}", flush=True)
http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
