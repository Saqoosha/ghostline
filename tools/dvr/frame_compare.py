"""Render the 3DGS at a frame's solved pose next to the DVR frame: DVR | render | render alpha | blend.
The page viewer does the same interactively; this is for looking at one frame without a browser, and for
checking what the tracker's filters see (it keeps matches only where the render's alpha exceeds 0.9).
usage (mastenv, with gsplat): frame_compare.py scene.ply dvr_pinhole.mp4 poses.json out_dir frame [frame ...]"""
import json, sys, os, numpy as np, torch, cv2
from plyfile import PlyData
from gsplat import rasterization
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from frustum import rasterize_culled   # centre-in-view culling, as PlayCanvas
from scipy.spatial.transform import Rotation as Rot
dev = "cuda"; ply, video, poses, out = sys.argv[1:5]; frames = [int(x) for x in sys.argv[5:]]; os.makedirs(out, exist_ok=True)
# NEAR (m): splats whose centre is closer than this are not drawn (see cpr_track.py)
NEAR = float(os.environ.get("NEAR", 0.01))
v = PlyData.read(ply)["vertex"].data; v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.1]
T = lambda a: torch.tensor(a, dtype=torch.float32, device=dev)
means, quats = T(np.c_[v["x"], v["y"], v["z"]]), T(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]])
scales, opac = torch.exp(T(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]])), torch.sigmoid(T(v["opacity"]))
sh0 = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :]; rest = np.stack([np.c_[[v[f"f_rest_{c*15+k}"] for k in range(15)]].T for c in range(3)], axis=2)
colors = T(np.concatenate([sh0, rest], axis=1)[:, :4])
cam = json.load(open(video + ".json")); W, H = cam["width"], cam["height"]
K = T([[cam["fx"], 0, cam["cx"]], [0, cam["fy"], cam["cy"]], [0, 0, 1]])
P = json.load(open(poses))["poses"]; cap = cv2.VideoCapture(video)
for i in frames:
    cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, dvr = cap.read()
    R = Rot.from_quat(P[i]["quat"]).as_matrix(); c = np.array(P[i]["pos"])
    vm = np.eye(4); vm[:3, :3] = R.T; vm[:3, 3] = -R.T @ c
    with torch.no_grad():
        o, a, _ = rasterize_culled(means, quats, scales, opac, colors, T(vm)[None], K[None], W, H, sh_degree=1, near_plane=NEAR)
    rgb = (o[0, ..., :3].clamp(0, 1) * 255).byte().cpu().numpy()[..., ::-1]; al = a[0, ..., 0].cpu().numpy()
    alv = cv2.applyColorMap((np.clip(al, 0, 1) * 255).astype(np.uint8), cv2.COLORMAP_VIRIDIS); alv[al > 0.9] = (255, 255, 255)
    blend = cv2.addWeighted(dvr, 0.5, rgb, 0.5, 0)
    top = np.hstack([dvr, rgb]); bot = np.hstack([alv, blend])
    cv2.imwrite(f"{out}/{i:05d}_dvr.png", dvr); cv2.imwrite(f"{out}/{i:05d}_render.png", rgb)
    cv2.imwrite(f"{out}/{i:05d}.jpg", cv2.resize(np.vstack([top, bot]), (W, H)), [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(i, P[i]["src"], f"alpha>0.9 on {np.mean(al > 0.9) * 100:.0f}% of the frame")
