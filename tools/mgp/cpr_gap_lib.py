"""The render-and-match pieces of cpr_gap.py, for cpr_reloc.py: the 3DGS in map coordinates rendered at a pose (gsplat,
culled as tools/dvr/frustum.py), a frame matched to a render with MASt3R and solved by PnP from the render's depth,
and a fisheye cell turned into the pinhole the two work in. Map units throughout (scene.U metres each)."""
import json, os, sys
import numpy as np, torch, cv2
from plyfile import PlyData
from scipy.spatial.transform import Rotation as Rot
sys.path.insert(0, os.path.expanduser("~/mast3r")); sys.path.insert(0, os.path.expanduser("~/rt"))
from frustum import rasterize_culled

dev = "cuda"
RW, RH = 512, 384
KP = np.array([[213.0, 0, RW / 2], [0, 213.0, RH / 2], [0, 0, 1]])        # the pinhole a fisheye cell is turned into
T = lambda a: torch.tensor(a, dtype=torch.float32, device=dev)


class Scene:
    def __init__(self, ply, frame_json, fit_json, near_m=0.5):
        F, Tf = json.load(open(frame_json)), json.load(open(fit_json))
        self.E, self.a0 = np.array([F["ex"], F["ey"], F["up"]]), np.array(F["origin"])
        self.R2 = np.array(Tf["R"]) @ np.diag([1, Tf["mirror"]]); self.t2 = np.array(Tf["t"]); self.U = float(Tf["scale"])
        self.near = near_m / self.U
        v = PlyData.read(ply)["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.1]
        self.means, self.quats = T(np.c_[v["x"], v["y"], v["z"]]), T(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]])
        self.scales, self.opac = torch.exp(T(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]])), torch.sigmoid(T(v["opacity"]))
        sh0 = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :]
        nrest = len([n for n in v.dtype.names if n.startswith("f_rest_")]) // 3
        rest = np.stack([np.c_[[v[f"f_rest_{c * nrest + k}"] for k in range(min(nrest, 3))]].T for c in range(3)], axis=2)
        self.colors = T(np.concatenate([sh0, rest], axis=1)[:, :4])
        print(f"{len(v):,} splats", flush=True)

    def metric(self, X):                              # map -> track frame (x = VD x, y = VD z, z = up), metres
        G = (np.atleast_2d(X) - self.a0) @ self.E.T * self.U
        return np.c_[G[:, :2] @ self.R2.T + self.t2, G[:, 2]]

    def render(self, R, c, small=False):
        """(Rot world-from-camera, centre) -> rgb uint8, depth, alpha, world-to-camera 4x4 (small: half size)"""
        s = 0.5 if small else 1.0; W, H = int(RW * s), int(RH * s); K = KP * [[s], [s], [1]]
        vm = np.eye(4); vm[:3, :3] = R.as_matrix().T; vm[:3, 3] = -vm[:3, :3] @ c
        with torch.no_grad():
            o, a, _ = rasterize_culled(self.means, self.quats, self.scales, self.opac, self.colors, T(vm[None]), T(K)[None], W, H,
                                       sh_degree=1, render_mode="RGB+ED", near_plane=self.near)
        return (o[0, ..., :3].clamp(0, 1) * 255).byte().cpu().numpy(), o[0, ..., 3].cpu().numpy(), a[0, ..., 0].cpu().numpy(), vm


class Matcher:
    def __init__(self, scene):
        from mast3r.model import AsymmetricMASt3R
        self.scene = scene
        self.model = AsymmetricMASt3R.from_pretrained("naver/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric").to(dev).eval()

    @staticmethod
    def _view(img, idx):
        return dict(img=torch.tensor(img, dtype=torch.float32).permute(2, 0, 1)[None] / 255 * 2 - 1, true_shape=np.int32([img.shape[:2]]), idx=idx, instance=str(idx))

    def solve(self, q, valid, R, c):
        """render at (R, c), match the frame q to it, PnP -> (Rot world-from-camera, centre, inliers) or None"""
        from mast3r.fast_nn import fast_reciprocal_NNs
        from dust3r.inference import inference
        rgb, dep, al, vm = self.scene.render(R, c)
        with torch.no_grad(): o = inference([(self._view(q, 0), self._view(rgb, 1))], self.model, dev, batch_size=1, verbose=False)
        a, b = fast_reciprocal_NNs(o["pred1"]["desc"][0], o["pred2"]["desc"][0], subsample_or_initxy1=4, device=dev, dist="dot", block_size=2**13)
        a, b = np.asarray(a), np.asarray(b); d = dep[b[:, 1], b[:, 0]]
        k = valid[a[:, 1], a[:, 0]] & (al[b[:, 1], b[:, 0]] > 0.9) & (d > self.scene.near) & (d < 200 / self.scene.U)
        a, b, d = a[k], b[k], d[k]
        if len(a) < 12: return None
        xc = np.c_[(b[:, 0] - KP[0, 2]) / KP[0, 0], (b[:, 1] - KP[1, 2]) / KP[1, 1], np.ones(len(b))] * d[:, None]
        X = (np.linalg.inv(vm) @ np.c_[xc, np.ones(len(xc))].T).T[:, :3]; uv = a.astype(np.float64)
        ok, rv, tv, inl = cv2.solvePnPRansac(X, uv, KP, None, iterationsCount=2000, reprojectionError=4.0, flags=cv2.SOLVEPNP_SQPNP)
        if not ok or inl is None: return None
        inl = inl[:, 0]; rv, tv = cv2.solvePnPRefineLM(X[inl], uv[inl], KP, None, rv, tv)
        Rw = cv2.Rodrigues(rv)[0]
        return Rot.from_matrix(Rw.T), -Rw.T @ tv[:, 0], int(len(inl))

    def solve_iter(self, q, valid, R, c, rounds=2):
        best = None
        for _ in range(rounds):                       # match again from the pose it found: a closer render matches more
            r = self.solve(q, valid, R, c)
            if r is None or (best and r[2] <= best[2]): break
            best = r; R, c = r[0], r[1]
        return best


def fisheye_to_pinhole(camera, images_dir, masks_dir):
    """the loc json's camera (OPENCV_FISHEYE) -> load(t_ms) -> (RGB pinhole RWxRH, valid mask: OSD/props and border out)"""
    fx, fy, cx, cy, *k = camera["params"]
    Kf = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])
    m1, m2 = cv2.fisheye.initUndistortRectifyMap(Kf, np.array(k[:4]), np.eye(3), KP, (RW, RH), cv2.CV_16SC2)

    def load(t_ms):
        img = cv2.imread(f"{images_dir}/{t_ms:07d}.jpg")
        mk = cv2.imread(f"{masks_dir}/{t_ms:07d}.jpg.png", cv2.IMREAD_GRAYSCALE)
        if mk is None: mk = np.full(img.shape[:2], 255, np.uint8)
        q = cv2.cvtColor(cv2.remap(img, m1, m2, cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
        valid = cv2.erode(cv2.remap(mk, m1, m2, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0), np.ones((9, 9), np.uint8)) > 0
        return q, valid
    return load
