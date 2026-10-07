#!/bin/bash
# rt_gpu_idle.sh: brings the GPU's idle power back down on the field box. Once CUDA has been used the 4090 idles at 21 W
# in P8 instead of 7 W and stays there; the driver's suspend + resume returns it to 7 W for as long as the processes alive
# at that moment live (about 2 s with the four-pilot tracker's context, and the tracker goes on) - see
# docs/realtime-tracking.ja.md, "電池で回す". Runs as root (ghostline-gpu-idle.service, installed by rt_linux_setup.sh).
# Fires only after SECS of P8 at WATTS or more: during a heat the GPU is in P2, so it never fires in the middle of one.
WATTS=${WATTS:-15} SECS=${SECS:-20}
read_gpu() { IFS=', ' read -r ps w <<< "$(nvidia-smi --query-gpu=pstate,power.draw --format=csv,noheader,nounits 2>/dev/null)"; w=${w%.*}; }
high() { [ "$ps" = P8 ] && [[ $w =~ ^[0-9]+$ ]] && [ "$w" -ge "$WATTS" ]; }
n=0; hold=60
while sleep 5; do
  read_gpu; if high; then n=$((n + 5)); else n=0; fi
  [ $n -lt "$SECS" ] && continue
  before=$w; echo suspend > /proc/driver/nvidia/suspend && echo resume > /proc/driver/nvidia/suspend
  sleep 30; read_gpu; n=0                # P0 for about 10 s after the resume, then P8 again
  if high; then echo "$before W in P8: suspend/resume did not help ($w W), next try in $hold s"; sleep $hold; hold=$((hold < 1800 ? hold * 2 : 3600))
  else echo "$before W in P8: suspend/resume -> $w W ($ps)"; hold=60; fi
done
