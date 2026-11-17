#!/usr/bin/env bash
# On a new Vast.ai instance (image nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04): the live tracker's environment, the same
# versions as rt4090's mastenv (torch 2.11 + cu128, gsplat 1.5.3 built here for the instance's GPUs, kornia 0.8.3,
# poselib 2.0.5), XFeat at the commit rt4090 uses, DINOv2 in torch's hub cache, and ffmpeg for the pinhole videos.
# Run once as root over ssh. Last line "SETUP OK" or "SETUP FAILED".
#   ssh -p <port> root@<host> 'bash -s' < tools/vast/udgp_setup.sh
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive PATH=/opt/env/bin:/root/.local/bin:$PATH   # /opt/env/bin: gsplat's build calls ninja
for p in $(pgrep -x apt-get) $(pgrep -x apt); do kill "$p" 2>/dev/null; done; sleep 1; dpkg --configure -a > /dev/null 2>&1
timeout 300 apt-get update -qq > /tmp/apt.log 2>&1
timeout 600 apt-get install -y -qq ffmpeg git rsync curl libgl1 libglib2.0-0 > /tmp/apt_install.log 2>&1; echo "apt rc $?"
command -v uv > /dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh > /dev/null 2>&1
uv venv -q -p 3.11 /opt/env
uv pip install -q -p /opt/env torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install -q -p /opt/env kornia==0.8.3 poselib==2.0.5 opencv-python-headless numpy scipy plyfile pillow ninja jaxtyping rich tqdm
uv pip install -q -p /opt/env --no-build-isolation gsplat==1.5.3
[ -d /root/xfeat ] || git clone -q https://github.com/verlab/accelerated_features /root/xfeat; git -C /root/xfeat checkout -q e92685f
# gsplat compiles its CUDA kernels on the first call; do it now, for this machine's GPUs only
GPU_CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1)
export TORCH_CUDA_ARCH_LIST="$GPU_CC"
/opt/env/bin/python - <<'EOF'
import torch, gsplat, kornia, poselib, cv2
from gsplat import rasterization
d = "cuda"; n = 1000
m = torch.randn(n, 3, device=d) + torch.tensor([0, 0, 5.0], device=d)
q = torch.nn.functional.normalize(torch.randn(n, 4, device=d), dim=1); s = torch.full((n, 3), 0.05, device=d)
o = torch.ones(n, device=d) * 0.5; c = torch.rand(n, 3, device=d)
K = torch.tensor([[[300.0, 0, 160], [0, 300.0, 120], [0, 0, 1]]], device=d)
img, _, _ = rasterization(m, q, s, o, c, torch.eye(4, device=d)[None], K, 320, 240)
print("gsplat ok", tuple(img.shape), "torch", torch.__version__, "gpus", torch.cuda.device_count(), torch.cuda.get_device_name(0))
torch.hub.load("facebookresearch/dinov2", "dinov2_vits14")
print("dinov2 ok")
EOF
rc=$?
echo "cores $(nproc), $(free -g | awk 'NR==2{print $2}') GB RAM, disk $(df -h /root | awk 'END{print $4}') free"
[ $rc = 0 ] && echo "SETUP OK" || echo "SETUP FAILED"
