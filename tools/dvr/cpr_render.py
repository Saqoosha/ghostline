"""GS-CPR step 1 (gsenv): render RGB + expected depth of the 3DGS at candidate poses around each frame's
start pose. Candidates are the start pose rotated about the camera's y axis (yaw) and x axis (pitch).
usage: cpr_render.py scene.ply dvr_pinhole.mp4.json poses.json frames.txt out_dir
env: YAWS (deg, comma list, default 0,-25,25; +-50 won 9 of 408 frames), PITCHES (default 0), W (render width, default 512)
Splats under opacity 0.1 are dropped and SH is cut to degree 1, as in refine_batch.py. Each npz holds rgb, depth,
alpha, w2c and K per candidate; frames without a start pose get none. The caller writes DONE when this exits."""
import json, sys, os, math, numpy as np, torch
from plyfile import PlyData
from gsplat import rasterization
dev = "cuda"
ply, camj, poses_json, frames_txt, out_dir = sys.argv[1:6]
YAWS = [float(x) for x in os.environ.get("YAWS", "0,-25,25").split(",")]
PITCHES = [float(x) for x in os.environ.get("PITCHES", "0").split(",")]
RW = int(os.environ.get("W", 512)); SH = 1
os.makedirs(out_dir, exist_ok=True)
v = PlyData.read(ply)["vertex"].data
v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.1]
means = torch.tensor(np.c_[v["x"], v["y"], v["z"]], dtype=torch.float32, device=dev)
quats = torch.tensor(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], dtype=torch.float32, device=dev)
scales = torch.exp(torch.tensor(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]], dtype=torch.float32, device=dev))
opac = torch.sigmoid(torch.tensor(v["opacity"], dtype=torch.float32, device=dev))
sh0 = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :]
rest = np.stack([np.c_[[v[f"f_rest_{c*15+k}"] for k in range(15)]].T for c in range(3)], axis=2)
colors = torch.tensor(np.concatenate([sh0, rest], axis=1)[:, :(SH + 1) ** 2], dtype=torch.float32, device=dev)
cam = json.load(open(camj)); s = RW / cam["width"]; RH = int(round(cam["height"] * s))
K = np.array([[cam["fx"] * s, 0, cam["cx"] * s], [0, cam["fy"] * s, cam["cy"] * s], [0, 0, 1]], np.float32)
Kt = torch.tensor(K, device=dev)
def qxyzw_to_R(q):
    x, y, z, w = q; return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)], [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)], [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
def rot(axis, deg):
    c, s_ = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    if axis == "y": return np.array([[c, 0, s_], [0, 1, 0], [-s_, 0, c]])
    return np.array([[1, 0, 0], [0, c, -s_], [0, s_, c]])
poses = {p["i"]: p for p in json.load(open(poses_json))["poses"] if p}
frames = [int(x) for x in open(frames_txt).read().split()]
print(f"{len(v):,} splats, {len(frames)} frames, {len(YAWS) * len(PITCHES)} candidates, {RW}x{RH}", flush=True)
for i in frames:
    if i not in poses or os.path.exists(f"{out_dir}/{i:05d}.npz"): continue
    R = qxyzw_to_R(poses[i]["quat"]); c = np.array(poses[i]["pos"])
    w2c = np.r_[np.c_[R.T, -R.T @ c], [[0, 0, 0, 1]]]
    vms = []
    for p in PITCHES:
        for y in YAWS:
            D = np.eye(4); D[:3, :3] = rot("x", p) @ rot("y", y); vms.append(D @ w2c)
    vm = torch.tensor(np.stack(vms), dtype=torch.float32, device=dev)
    with torch.no_grad():
        out, alpha, _ = rasterization(means, quats, scales, opac, colors, vm, Kt[None].expand(len(vms), 3, 3), RW, RH, sh_degree=SH, render_mode="RGB+ED")
    rgb = (out[..., :3].clamp(0, 1) * 255).byte().cpu().numpy(); depth = out[..., 3].cpu().numpy().astype(np.float16)
    np.savez(f"{out_dir}/{i:05d}.tmp.npz", rgb=rgb, depth=depth, alpha=alpha[..., 0].cpu().numpy().astype(np.float16), w2c=np.stack(vms).astype(np.float64), K=K)
    os.replace(f"{out_dir}/{i:05d}.tmp.npz", f"{out_dir}/{i:05d}.npz")   # the matcher reads only finished files
open(f"{out_dir}/DONE", "w").close()
print("RENDER-DONE", flush=True)
