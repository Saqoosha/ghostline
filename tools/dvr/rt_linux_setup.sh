#!/bin/bash
# rt_linux_setup.sh: the live tracker on native Ubuntu 24.04 (the 4090 box's 150 GiB on the 750 EVO), as WSL's ~/mastenv.
# Run as saqoosha with sudo after a plain Ubuntu Server install. Steps are re-runnable; each says what it waits for.
# Why native: under WSL every blocking CUDA call spins a core (BLOCKING_SYNC is ignored) and each CUDA call crosses to
# Windows as an ioctl - see docs/realtime-tracking.ja.md, "電池で回す".
set -euo pipefail
step() { printf '\n== %s\n' "$*"; }

step "packages (build tools, GStreamer with SRT for the live input, ffmpeg, avahi so NDI finds its sources over mDNS)"
sudo apt-get update
sudo apt-get install -y build-essential git curl ffmpeg ntfs-3g avahi-daemon \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav

step "NVIDIA driver (headless, open kernel modules); reboot afterwards if nvidia-smi fails"
if ! nvidia-smi >/dev/null 2>&1; then
  sudo ubuntu-drivers install --gpgpu   # 595-server-open on 2026-10-06; --gpgpu leaves out nvidia-smi
  sudo apt-get install -y "nvidia-utils-$(dpkg -l | grep -o "nvidia-headless-no-dkms-[0-9]*-server" | head -1 | sed "s/nvidia-headless-no-dkms-//")"
  echo "driver installed: reboot, then run this script again"; exit 0
fi
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader

step "CUDA toolkit 12.9 (gsplat builds its kernels on first use and needs nvcc; WSL builds it with 12.9 against torch cu128)"
if [ ! -x /usr/local/cuda-12.9/bin/nvcc ]; then
  curl -fsSLo /tmp/cuda-keyring.deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
  sudo dpkg -i /tmp/cuda-keyring.deb && sudo apt-get update && sudo apt-get install -y cuda-toolkit-12-9
fi
export CUDA_HOME=/usr/local/cuda-12.9 PATH=/usr/local/cuda-12.9/bin:$PATH
grep -q cuda-12.9 ~/.bashrc || echo 'export CUDA_HOME=/usr/local/cuda-12.9 PATH=/usr/local/cuda-12.9/bin:$PATH' >> ~/.bashrc

step "Windows' C: read-only at /mnt/c, so the paths match WSL's (scenes, flights, maps, the torch hub cache)"
sudo mkdir -p /mnt/c
grep -q ' /mnt/c ' /etc/fstab || echo 'LABEL=Windows /mnt/c ntfs3 ro,nofail,uid=1000,gid=1000 0 0' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload; sudo mount -a; ls /mnt/c/Users/saqoosha/VDGS/dvr/rt >/dev/null

step "python 3.11 venv ~/mastenv with WSL's versions"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH=$HOME/.local/bin:$PATH
[ -d ~/mastenv ] || uv venv ~/mastenv --python 3.11
uv pip install -p ~/mastenv torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install -p ~/mastenv numpy scipy opencv-python==5.0.0.93 kornia==0.8.3 plyfile poselib==2.0.5 \
  tensorrt-cu12==11.3.0.99 onnx onnxscript ninja tqdm gsplat==1.5.3 cyndilib   # cyndilib: NDI input, bundles libndi

step "XFeat (code and weights) and the torch hub cache (DINOv2) from WSL's copies on C:"
[ -d ~/xfeat ] || cp -r /mnt/c/Users/saqoosha/VDGS/dvr/rt/pw/xfeat ~/xfeat
mkdir -p ~/.cache/torch && [ -d ~/.cache/torch/hub ] || cp -r /mnt/c/Users/saqoosha/.cache/torch/hub ~/.cache/torch/

step "working copy ~/rt: C: is read-only, so the tracker runs from a copy (about 3.5 GB: the test's maps and truths, the scene, the flights)"
mkdir -p ~/rt/pw ~/scenes
# not all of rt/ (39 GB of earlier runs): the scripts, the four maps, the truths
(cd /mnt/c/Users/saqoosha/VDGS/dvr/rt && cp --update=none *.py map_fdf-r6b-d05.npz map_race-sf-*.npz truth-*.json race-sf-*.json scan_cameras.json ~/rt/)
cp --update=none /mnt/c/Users/saqoosha/VDGS/scenes/FDF-2026-R6b-spirula-web-dvr2.ply ~/scenes/
for f in fdf-r6b-d05 race-sf-knt race-sf-saqoosha race-sf-sena; do
  mkdir -p ~/$f && cp --update=none /mnt/c/Users/saqoosha/VDGS/dvr/$f/dvr_pinhole.mp4* ~/$f/
done

step "gsplat's first build (a few minutes) and TensorRT engines for this OS (eng_linux)"
cd ~/rt
# the build runs on the first render, not on import, and needs ninja (in the venv) and nvcc on PATH
PATH=$HOME/mastenv/bin:$PATH ~/mastenv/bin/python -c "import torch; from gsplat import rasterization as r; d='cuda'; r(torch.zeros(1,3,device=d)+torch.tensor([0,0,5.],device=d), torch.tensor([[1.,0,0,0]],device=d), torch.full((1,3),.1,device=d), torch.ones(1,device=d), torch.ones(1,3,device=d), torch.eye(4,device=d)[None], torch.tensor([[[300.,0,320],[0,300,240],[0,0,1]]],device=d), 640, 480); print('gsplat built')"
[ -f eng_linux/xfeat_fp32.engine ] || ~/mastenv/bin/python rt_trt.py build eng_linux glue_mix xfeat_fp32

step "a field box: local-time RTC (Windows shares the clock), and only what the tracker needs keeps running"
# Linux writes the RTC as UTC by default and Windows then runs 9 h behind after every Linux boot
sudo timedatectl set-timezone Asia/Tokyo; sudo timedatectl set-local-rtc 1
# off, not removed (systemctl enable brings any back). No updates in the middle of an event; nothing here uses snaps, cloud-init,
# multipath, iSCSI, LVM or RAID. Idle GPU + CPU package 30 -> 19 W on 2026-10-06 (GPU 22 -> 10 W; which unit woke it is not pinned down)
sudo systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer \
  snapd.service snapd.socket snapd.seeded.service snapd.apparmor.service snapd.autoimport.service snapd.core-fixup.service \
  snapd.recovery-chooser-trigger.service snapd.system-shutdown.service snapd.snap-repair.timer lxd-installer.socket cloud-init-hotplugd.socket \
  multipathd.service multipathd.socket open-iscsi.service iscsid.socket lvm2-monitor.service dm-event.socket lvm2-lvmpolld.socket \
  blk-availability.service mdcheck_continue.timer mdcheck_start.timer mdmonitor-oneshot.timer udisks2.service ModemManager.service \
  open-vm-tools.service vgauth.service apport.service apport-autoreport.path apport-autoreport.timer apport-forward.socket pollinate.service \
  ubuntu-advantage.service ua-timer.timer ua-reboot-cmds.service motd-news.timer update-notifier-download.timer update-notifier-motd.timer \
  fwupd-refresh.timer sysstat.service sysstat-collect.timer sysstat-summary.timer man-db.timer gpu-manager.service rsyslog.service \
  systemd-networkd-wait-online.service 2>/dev/null || true
sudo touch /etc/cloud/cloud-init.disabled

step "the GPU's idle power: 21 W in P8 once CUDA has run, 7 W again after the driver's suspend + resume (rt_gpu_idle.sh does it)"
sudo install -m 755 "$(dirname "$0")/rt_gpu_idle.sh" /usr/local/sbin/rt_gpu_idle.sh
sudo tee /etc/systemd/system/ghostline-gpu-idle.service > /dev/null <<'UNIT'
[Unit]
Description=ghostline: bring the GPU's idle power back down after CUDA use
After=nvidia-persistenced.service

[Service]
ExecStart=/usr/local/sbin/rt_gpu_idle.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload; sudo systemctl enable --now ghostline-gpu-idle.service

step "the control page (rt_control.py): start / stop the tracker on an NDI source from a browser, http://<this box>:8080"
cp "$(dirname "$0")/rt_control.py" "$(dirname "$0")/rt_control.html" ~/rt/
sudo tee /etc/systemd/system/ghostline-control.service > /dev/null <<UNIT
[Unit]
Description=ghostline: the live tracker's control page
After=network-online.target avahi-daemon.service nvidia-persistenced.service

[Service]
User=$USER
WorkingDirectory=$HOME/rt
ExecStart=$HOME/mastenv/bin/python -u rt_control.py
Restart=always
RestartSec=5
# stopping the service stops the tracker it started; the tracker writes its files on TERM
TimeoutStopSec=45

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload; sudo systemctl enable --now ghostline-control.service

step "done: a paced 4-pilot run to compare with WSL"
echo 'cd ~/rt && GLUE=eng_linux/glue_mix.engine XFEAT=eng_linux/xfeat_fp32.engine RENDER=1 RCLIP=2 PACE=1 CUDA_SYNC=block \'
echo '  SCENE=~/scenes/FDF-2026-R6b-spirula-web-dvr2.ply ~/mastenv/bin/python rt_track.py map_fdf-r6b-d05.npz,../fdf-r6b-d05/dvr_pinhole.mp4,pw/o_d05,truth-d05.json ...'
