"""How long the tracker's RENDER=1 draw takes for a scene, and what the GPU draws meanwhile: the rasterization of rt_render.py
(sh 1, RGB+ED, RCLIP=2, the frustum cull), four poses per call as four pilots, at the map's camera size. Run on rt4090 with the
tracker stopped. Not the XFeat on the render: the same for any scene.
  python render_bench.py <scene.ply> <map.npz> <map_poses.json> [REPS=300]   (from ~/rt: frustum.py beside it)"""
import json, os, subprocess, sys, threading, time
os.environ.setdefault("RCLIP", "2")
import numpy as np, torch
from plyfile import PlyData
from scipy.spatial.transform import Rotation as Rot
from frustum import rasterize_culled
ply, mp, poses_f = sys.argv[1:4]; reps = int(sys.argv[4]) if len(sys.argv) > 4 else 300
m = np.load(mp); K = np.array(m["K"], float); W, H = [int(v) for v in m["size"]]
T = lambda a: torch.tensor(a, dtype=torch.float32, device="cuda")
v = PlyData.read(ply)["vertex"].data; n_all = len(v); v = v[1 / (1 + np.exp(-v["opacity"])) >= 0.1]
means, quats = T(np.c_[v["x"], v["y"], v["z"]]), T(np.c_[v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]])
scales, opac = torch.exp(T(np.c_[v["scale_0"], v["scale_1"], v["scale_2"]])), torch.sigmoid(T(v["opacity"]))
sh0 = np.c_[v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]][:, None, :]
rest = np.stack([np.c_[[v[f"f_rest_{c*15+k}"] for k in range(15)]].T for c in range(3)], axis=2)
colors = T(np.concatenate([sh0, rest], axis=1)[:, :4]); del v, rest
cams = json.load(open(poses_f))["cameras"]; rng = np.random.default_rng(0)
sets = []
for _ in range(32):                                  # 32 sets of 4 poses from the map's views, cycled
    vm = []
    for c in rng.choice(cams, 4, replace=False):
        Rm = Rot.from_quat(c["quat"]).as_matrix(); M = np.eye(4); M[:3, :3] = Rm.T; M[:3, 3] = -Rm.T @ np.array(c["pos"]); vm.append(M)
    sets.append(T(np.stack(vm)))
Kt = T(np.stack([K] * 4)); kw = dict(sh_degree=1, render_mode="RGB+ED", near_plane=1.0, radius_clip=2.0)
draw = lambda vmt: rasterize_culled(means, quats, scales, opac, colors, vmt, Kt, W, H, **kw)
for k in range(20): draw(sets[k % 32])                # warm up
torch.cuda.synchronize()
watts, stop = [], threading.Event()
def sample():
    p = subprocess.Popen(["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits", "-lms", "100"], stdout=subprocess.PIPE, text=True)
    for line in p.stdout:
        if stop.is_set(): break
        try: watts.append(float(line))
        except ValueError: pass
    p.terminate()
th = threading.Thread(target=sample, daemon=True); th.start(); time.sleep(1.0)
ms = []
with torch.no_grad():
    for k in range(reps):
        t0 = time.perf_counter(); draw(sets[k % 32]); torch.cuda.synchronize(); ms.append((time.perf_counter() - t0) * 1000)
        time.sleep(0.009)                            # about the tracker's pace: one 4-pilot round every ~20 ms, not back to back
stop.set(); th.join(2)
w = np.array(watts[10:-2] or watts)
print(f"{os.path.basename(ply)}: {n_all:,} splats ({len(means):,} drawn, opacity >= 0.1), {W}x{H}, 4 views a call: "
      f"p50 {np.median(ms):.1f} ms, p90 {np.percentile(ms, 90):.1f} ms; GPU {w.mean():.0f} W mean, {np.percentile(w, 90):.0f} W p90 ({len(w)} samples)")
