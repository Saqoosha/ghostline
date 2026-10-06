"""rt_topview.py: the scan seen from straight above, one image for the control page's top view (rt_control.py runs it once per scene
and map and keeps the file). An orthographic camera looking down - x east to the right, z south down the image, so north is up -
so a pixel is a fixed length on the ground and the page lays positions over it without a projection.
usage (mastenv): rt_topview.py scene.ply out.jpg x0 z0 width height y_top [pixels per metre, 10]
  x0 z0 width height: the ground rectangle, metres; y_top: the camera's height. Splats above it (sky, the tops of far trees) are
  behind the camera and left out, so it should sit just over the highest flight."""
import sys, numpy as np, torch, cv2
from gsplat import rasterization
from rt_render import Scene
ply, out = sys.argv[1:3]; x0, z0, w, h, ytop = (float(v) for v in sys.argv[3:8]); ppm = float(sys.argv[8]) if len(sys.argv) > 8 else 10
W, H = round(w * ppm), round(h * ppm)
sc = Scene(ply, W, H, 0.1, 0, None)
# the camera's axes in the world (COLMAP: x right, y down, z forward; the world: x east, y up, z south)
Rwc = np.stack([[1, 0, 0], [0, 0, 1], [0, -1, 0]], axis=1).astype(float); c = np.array([x0 + w / 2, ytop, z0 + h / 2])
vm = np.eye(4); vm[:3, :3] = Rwc.T; vm[:3, 3] = -Rwc.T @ c
K = np.array([[ppm, 0, W / 2], [0, ppm, H / 2], [0, 0, 1]])   # orthographic: fx, fy are pixels per metre
with torch.no_grad():
    o, a, _ = rasterization(sc.means, sc.quats, sc.scales, sc.opac, sc.colors, sc.T(vm)[None], sc.T(K)[None], W, H, sh_degree=1, camera_model="ortho",
                            near_plane=0.1, far_plane=1000)
    img = o[0, ..., :3].clamp(0, 1) + (1 - a[0]) * sc.T([0.953, 0.957, 0.965])   # the page's paper where the scan has nothing
cv2.imwrite(out, cv2.cvtColor((img.clamp(0, 1) * 255).byte().cpu().numpy(), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 82])
print(f"{out}: {W}x{H}, {len(sc.means)} splats", flush=True)
