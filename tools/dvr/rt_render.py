"""Keyframes rendered on the spot for rt_track.py (RENDER=1): the 3DGS drawn at each stream's predicted pose, XFeat on
the render, its points lifted to 3D with the rendered depth - what rt_map.py stores, made for the pose itself instead
of looked up among the stored ones. On d05 (offline, 991 frames, PyTorch matcher, PoseLib) matching the frame against
this one render instead of the 2 nearest keyframes took the answer from p50 0.193 / p90 0.71 m to 0.121 / 0.61 m
(0.112 / 0.51 m with a second render at that answer); adding the keyframes' matches back made it worse (0.138 m).
A render and XFeat on it take ~7 ms (640 x 480, 4090)."""
import os, numpy as np, torch
from plyfile import PlyData
from scipy.spatial.transform import Rotation as Rot
from frustum import rasterize_culled, keep_mask
from gsplat import rasterization
RCLIP, ROPA, RBATCH = float(os.environ.get("RCLIP", 0)), float(os.environ.get("ROPA", 0.1)), int(os.environ.get("RBATCH", 0))
REDGE = float(os.environ.get("REDGE", 0))   # drop points whose 5x5 depth range is over REDGE x their depth (0 = keep all)   # skip splats under RCLIP px across / below ROPA opacity


class Scene:
    def __init__(self, ply, W, H, near, topk, detect):
        """detect(img [B, 3, H, W] in 0..1) -> (keypoints [B, topk, 2], descriptors [B, topk, 64], score [B, topk], -1 = none)"""
        dev = "cuda"; T = lambda a: torch.tensor(a, dtype=torch.float32, device=dev)
        v = PlyData.read(ply)["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= ROPA]   # 0.1 as rt_map.py
        self.means, self.quats = T(np.c_[v["x"], v["y"], v["z"]]), T(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]])
        self.scales, self.opac = torch.exp(T(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]])), torch.sigmoid(T(v["opacity"]))
        sh0 = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :]
        rest = np.stack([np.c_[[v[f"f_rest_{c*15+k}"] for k in range(15)]].T for c in range(3)], axis=2)
        self.colors = T(np.concatenate([sh0, rest], axis=1)[:, :4])
        self.W, self.H, self.near, self.topk, self.detect, self.T = W, H, near, topk, detect, T

    def views(self, poses, Ks):
        """[(Rot world-from-camera, position)], [3x3 intrinsics] -> per pose dict(kp [topk, 2], desc [topk, 64] on the GPU, X [topk, 3] numpy, n):
        the first n points have depth, the rest are zero padding (as a map keyframe)"""
        vm = np.stack([np.eye(4) for _ in poses])
        for m, (R, c) in zip(vm, poses): Rm = R.as_matrix(); m[:3, :3] = Rm.T; m[:3, 3] = -Rm.T @ c
        vmt, Kt = self.T(vm), self.T(np.stack(Ks)); B = len(poses)
        with torch.no_grad():
            kw = dict(sh_degree=1, render_mode="RGB+ED", near_plane=self.near, radius_clip=RCLIP)
            if RBATCH and B > 1:                    # one call for all cameras on the union of their culls: half the time of one by one
                i = keep_mask(self.means, vmt, Kt, self.W, self.H, self.near).any(0).nonzero()[:, 0]
                o, a, _ = rasterization(self.means[i], self.quats[i], self.scales[i], self.opac[i], self.colors[i], vmt, Kt, self.W, self.H, **kw)
            else: o, a, _ = rasterize_culled(self.means, self.quats, self.scales, self.opac, self.colors, vmt, Kt, self.W, self.H, **kw)
            kp, desc, sc = self.detect(o[..., :3].clamp(0, 1).permute(0, 3, 1, 2))
            ix = kp.round().long(); ix[..., 0].clamp_(0, self.W - 1); ix[..., 1].clamp_(0, self.H - 1); bi = torch.arange(B, device=kp.device)[:, None]
            d, al = o[..., 3][bi, ix[..., 1], ix[..., 0]], a[..., 0][bi, ix[..., 1], ix[..., 0]]
            ok = (sc > 0) & (al > 0.9) & (d > 0.3) & (d < 200)
            if REDGE:                               # on a depth edge the rendered depth mixes near and far: the lifted point is off
                dm = o[..., 3][:, None]; rng = (torch.nn.functional.max_pool2d(dm, 5, 1, 2) + torch.nn.functional.max_pool2d(-dm, 5, 1, 2))[:, 0]
                ok &= rng[bi, ix[..., 1], ix[..., 0]] < REDGE * d
            order = torch.argsort((~ok).int(), dim=1, stable=True)   # points with depth first, in the detector's order
            g = lambda t: torch.gather(t, 1, order[..., None].expand(-1, -1, t.shape[-1]))
            kp, desc, d, ok = g(kp), g(desc), torch.gather(d, 1, order), torch.gather(ok, 1, order)
            f, cc = Kt[:, None, [0, 1], [0, 1]], Kt[:, None, [0, 1], [2, 2]]
            xc = torch.cat([(kp - cc) / f, torch.ones_like(d)[..., None]], -1) * d[..., None]
            X = torch.einsum("bij,bnj->bni", torch.linalg.inv(vmt)[:, :3, :3], xc) + torch.linalg.inv(vmt)[:, None, :3, 3]
            kp, desc, X = kp * ok[..., None], desc * ok[..., None], X * ok[..., None]
            n, X = ok.sum(1).cpu().numpy(), X.cpu().numpy()
        return [dict(kp=kp[b], desc=desc[b], X=X[b], n=int(n[b])) for b in range(B)]
