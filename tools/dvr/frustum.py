"""Leave out, per camera, the splats whose centre is outside the view - as PlayCanvas (SuperSplat, the viewer) does.
gsplat draws every splat in front of the near plane, and a small splat far off to the side, nearly level with the
camera (depth along the view a fraction of its sideways distance), comes out of the perspective Jacobian as a smear
thousands of pixels wide. FDF d05 #1435: splats of 3-60 cm, 47 m away beside the camera, projected to sigma 1,000-
3,000,000 px and painted the sky grey (108 against PlayCanvas' 215-230 and the DVR's 170-176); dropping the 200
worst gave 212. Culling by centre with CULL x the half field of view fixes it: the mean difference to the PlayCanvas
render of that frame went 24.3 -> 11.2 at 1.0x, 10.0 at 1.2x, 9.6 at 1.5x, 9.4 at 2.0x. 1.2 is what the mod
(SplatUtilities.compute _CullCenterSlack) and the web viewers use, so the matcher sees what the game draws.
gsplat 1.5.3 takes one opacity per splat for all cameras (not [C, N]), so rasterize_culled draws each camera on its own
subset and stacks the outputs; the tracker's time is MASt3R's, not the render's.
env: CULL (1.2; 0 = draw everything, as before)"""
import os, torch
from gsplat import rasterization
CULL = float(os.environ.get("CULL", 1.2))
def keep_mask(means, viewmats, Kc, W, H, near=0.01):
    """[C, N] bool: the splat's centre is inside CULL x the view of camera c (viewmats [C, 4, 4] world-to-camera, Kc [C, 3, 3])"""
    p = torch.einsum("cij,nj->cni", viewmats[:, :3, :3], means) + viewmats[:, None, :3, 3]       # [C, N, 3]
    z = p[..., 2]; tx = (W / 2) / Kc[:, 0, 0]; ty = (H / 2) / Kc[:, 1, 1]
    zc = z.clamp(min=1e-6)
    return (z > near) & ((p[..., 0] / zc).abs() < CULL * tx[:, None]) & ((p[..., 1] / zc).abs() < CULL * ty[:, None])
def rasterize_culled(means, quats, scales, opac, colors, viewmats, Ks, W, H, **kw):
    """gsplat.rasterization with each camera drawn from the splats whose centre is in its view; returns (render, alpha, None)"""
    if CULL <= 0: return rasterization(means, quats, scales, opac, colors, viewmats, Ks, W, H, **kw)
    keep = keep_mask(means, viewmats, Ks, W, H, kw.get("near_plane", 0.01)); outs, alphas = [], []
    for c in range(viewmats.shape[0]):
        i = keep[c].nonzero()[:, 0]
        o, a, _ = rasterization(means[i], quats[i], scales[i], opac[i], colors[i], viewmats[c:c + 1], Ks[c:c + 1], W, H, **kw)
        outs.append(o); alphas.append(a)
    return torch.cat(outs), torch.cat(alphas), None
