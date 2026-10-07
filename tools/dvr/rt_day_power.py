"""rt_day_power.py: the energy the tracker box needs for an event day, from rt_day_gate.py's samples of the day's broadcast (two a
second: race layout, picture in each of the four pilot cells): the time with 0..4 cells carrying a picture times the power measured
in each state.
Power (GPU + CPU package, W; rt4090 on Linux, NDI 1080p 30 fps in a 2x2, RENDER=1, measured on the FDF semifinal - the numbers and
how they were taken are in docs/realtime-tracking.ja.md): by cells with a picture. 4 was never seen in that recording: taken from the
step 2 -> 3 (+36 W). Empty: 71 W for the first 20 s (the GPU lingers in P2), then 32 W as it is, or 19 W once the idle watchdog has
fired (GPU 7 W). At the wall: + REST for the box's other parts, over the supply's efficiency - both assumed, not measured.
Screens that are not the race layout count as no picture; "if those were like the rest" is the upper bound printed beside it.
usage: rt_day_power.py <gate.npy> ...               # one file per recording of the day"""
import sys, numpy as np
P = {1: 110, 2: 166, 3: 202, 4: 238}; LINGER, IDLE, IDLE_WD = 71, 32, 19; REST, PSU, WH = 40, 0.90, 1760   # WH: an EcoFlow DELTA 2 Max through its AC outlet
dt = 0.5; tot = dict(t=0.0, e=0.0, e_wd=0.0, e_up=0.0, hist=np.zeros(5))
for f in sys.argv[1:]:
    a = np.load(f); live = a[:, 0]; n = (a[:, 1:] & live[:, None]).sum(1); T = len(n) * dt
    since = np.zeros(len(n)); c = 1e9                # seconds since any cell last had a picture
    for i in range(len(n)): c = 0 if n[i] else c + dt; since[i] = c
    lin = (n == 0) & (since <= 20)
    w = np.where(n > 0, np.array([P.get(int(k), 0) for k in n]), np.where(lin, LINGER, IDLE)).astype(float); w_wd = np.where((n == 0) & ~lin, IDLE_WD, w)
    d = np.diff(np.r_[0, (n > 0).astype(int), 0]); runs = [(b - a_) * dt for a_, b in zip(np.where(d == 1)[0], np.where(d == -1)[0]) if (b - a_) * dt >= 20]
    hist = np.array([(n == k).mean() for k in range(5)]); up = w[live].mean() if live.any() else w.mean()   # as if the other screens were like the race layout's time
    print(f"{f}: {T / 3600:.2f} h, race layout {live.mean() * 100:.0f}% | cells with a picture 0/1/2/3/4: {' / '.join(f'{h * 100:.0f}%' for h in hist)} | mean cells {n.mean():.2f}"
          f" | stretches with a picture (>= 20 s): {len(runs)}, median {np.median(runs):.0f} s | mean {w.mean():.0f} W (idle watchdog {w_wd.mean():.0f} W, upper bound {up:.0f} W)")
    tot["t"] += T; tot["e"] += w.sum() * dt / 3600; tot["e_wd"] += w_wd.sum() * dt / 3600; tot["e_up"] += up * T / 3600; tot["hist"] += hist * T
T = tot["t"]; print(f"\nwhole day: {T / 3600:.2f} h | cells with a picture 0/1/2/3/4: {' / '.join(f'{x * 100:.0f}%' for x in tot['hist'] / T)}")
for name, e in (("as it is", tot["e"]), ("with the idle watchdog", tot["e_wd"]), ("upper bound (other screens too)", tot["e_up"])):
    mean = e / (T / 3600); wall = (mean + REST) / PSU
    print(f"  {name:32s} GPU+CPU {mean:4.0f} W, {e:5.0f} Wh | at the wall (+{REST} W, supply {PSU:.0%}) {wall:4.0f} W, {wall * T / 3600:5.0f} Wh = {wall * T / 3600 / WH * 100:3.0f}% of {WH} Wh | lasts {WH / wall:4.1f} h")
