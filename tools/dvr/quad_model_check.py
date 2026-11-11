"""Does a solved flight obey the quadrotor model, and would the model add information to the path?
Quadrotor: specific force f = a - g lies along the thrust axis, plus rotor drag roughly linear in body velocity.
The blackbox accelerometer measures f in the body frame directly, so it is the reference here.
usage: quad_model_check.py <flight dir> [poses.json]"""
import json, sys, numpy as np
from scipy.signal import savgol_filter
from scipy.spatial.transform import Rotation as Rot

D = sys.argv[1]; J = json.load(open(f"{D}/{sys.argv[2] if len(sys.argv) > 2 else 'poses60.json'}"))
P, fps, t0, takeoff = J["poses"], J["fps"], J["t0"], J.get("takeoff", 0)
S = json.load(open(f"{D}/imu_sync.json")); Rbc = np.array(S["R_body_from_cam"])
n = len(P); dt = 1 / fps
pos = np.array([p["pos"] for p in P]); Rwc = Rot.from_quat([p["quat"] for p in P])
src = np.array([p["src"] for p in P]); fr = np.arange(n)

bb = np.genfromtxt(f"{D}/blackbox.csv", delimiter=",", names=True)
tb = (bb["time"] - bb["time"][0]) / 1e6
gyro = np.c_[bb["gyroADC0"], bb["gyroADC1"], bb["gyroADC2"]]                    # deg/s
acc = np.c_[bb["accSmooth0"], bb["accSmooth1"], bb["accSmooth2"]] * 9.80665 / 2048  # m/s^2, specific force
print(f"blackbox {len(tb)} rows, {tb[-1]:.1f} s, {1/np.median(np.diff(tb)):.0f} Hz; poses {n} at {fps} fps, takeoff #{takeoff}")

# time sync: |omega| of the solved orientation against |gyro|
q = Rwc.as_matrix(); w = np.zeros(n)
rel = (Rwc[:-2].inv() * Rwc[2:]).as_rotvec(); w[1:-1] = np.degrees(np.linalg.norm(rel, axis=1)) / (2 * dt)
gm = np.linalg.norm(gyro, axis=1); cs = np.cumsum(np.r_[0, gm])
def box(sig_cs, t):     # mean of a 1 kHz signal over one video frame centred at t (blackbox seconds)
    a = np.searchsorted(tb, t - dt / 2); b = np.searchsorted(tb, t + dt / 2); b = np.maximum(b, a + 1)
    a = np.clip(a, 0, len(tb) - 1); b = np.clip(b, 1, len(tb)); return (sig_cs[b] - sig_cs[a]) / (b - a)
fly = (fr > takeoff + 30) & (src == "ba")
best = None
for sign in (1, -1):
    for c in np.arange(-3, 3, 0.004):
        # video frame i is at blackbox time  i*dt + c  (sign=+1)  or relative to the stored offset (sign=-1)
        t = fr * dt + c if sign == 1 else fr * dt + t0 - S["offset"] + c
        ok = fly & (t > 0.1) & (t < tb[-1] - 0.1)
        if ok.sum() < 500: continue
        r = np.corrcoef(w[ok], box(cs, t[ok]))[0, 1]
        if best is None or r > best[0]: best = (r, sign, c, t.copy())
r, sign, c, tbb = best
print(f"sync: |omega| correlation {r:.3f}, frame i at blackbox t = i/fps {'+ t0 - offset ' if sign < 0 else ''}{c:+.3f} s  (stored offset {S['offset']:.3f}, t0 {t0})")
inb = (tbb > 0.2) & (tbb < tb[-1] - 0.2)
acs = [np.cumsum(np.r_[0, acc[:, k]]) for k in range(3)]
fb_meas = np.c_[[box(acs[k], np.clip(tbb, 0.01, tb[-1] - 0.01)) for k in range(3)]].T     # (n,3) body frame, per frame

g = np.array([0, -9.80665, 0.0])
Rbw = Rot.from_matrix(Rbc) * Rwc.inv()                                    # body from world
def stats(x): return f"p50 {np.percentile(x, 50):5.2f}  p90 {np.percentile(x, 90):5.2f}"
for win in (9, 15, 25, 41, 61):
    h = win // 2
    a = savgol_filter(pos, win, 3, deriv=2, delta=dt, axis=0); v = savgol_filter(pos, win, 3, deriv=1, delta=dt, axis=0)
    aw = Rbw.inv().apply(fb_meas) + g; aw[~inb] = 0                       # measured acceleration, world frame
    pp = np.cumsum(np.cumsum(aw, axis=0), axis=0) * dt * dt               # pseudo-position; its drift is linear, the cubic fit removes it
    fm = Rbw.apply(savgol_filter(pp, win, 3, deriv=2, delta=dt, axis=0) - g)   # reference through the identical filter
    fp = Rbw.apply(a - g); vb = Rbw.apply(v)                              # path specific force and velocity, body frame
    good = np.convolve((src == "ba").astype(float), np.ones(win), "same") > win - 0.5
    m = good & inb & (fr > takeoff + 60) & (fr < n - 60)
    # thrust axis in the body frame from the accelerometer itself
    u = np.linalg.svd(fm[m], full_matrices=False)[2][0]; u *= np.sign(fm[m] @ u).mean() > 0 and 1 or -1
    e1 = np.cross(u, [1, 0, 0]); e1 /= np.linalg.norm(e1); e2 = np.cross(u, e1); L = np.c_[e1, e2]   # lateral basis
    lat_m, lat_p, lat_v = fm[m] @ L, fp[m] @ L, vb[m] @ L
    K = np.linalg.lstsq(lat_v, lat_m, rcond=None)[0]                      # lateral specific force = v_lat @ K (rotor drag)
    k_iso = (lat_v * lat_m).sum() / (lat_v ** 2).sum()
    res_model0 = np.linalg.norm(lat_m, axis=1)                            # model "no lateral force" against the truth
    res_model1 = np.linalg.norm(lat_m - lat_v @ K, axis=1)                # model with linear drag (velocity from the path)
    res_path = np.linalg.norm(lat_p - lat_m, axis=1)                      # what the solved path says against the truth
    res_ax = np.abs(fp[m] @ u - fm[m] @ u); slope = ((fp[m] @ u) * (fm[m] @ u)).sum() / ((fm[m] @ u) ** 2).sum()
    mm = lambda e: e / (2 * np.pi * (0.443 * fps / win * 2.2)) ** 2 * 1000
    # vision only: the direction the path's specific force keeps in the camera frame
    fc = Rwc.inv().apply(a - g)[m]; uc = np.linalg.svd(fc, full_matrices=False)[2][0]; uc *= 1 if (fc @ uc).mean() > 0 else -1
    ang = np.degrees(np.arccos(np.clip(uc @ (Rbc.T @ u), -1, 1)))
    fc_hz = 0.443 * fps / win * 2.2                                       # rough -3 dB of a cubic SG filter
    print(f"\nwindow {win} frames ({win*dt:.2f} s, ~{fc_hz:.1f} Hz), {m.sum()} frames")
    if win == 15: print(f"  thrust axis in body frame {np.round(u, 3)}, |f| {stats(np.linalg.norm(fm[m], axis=1))} m/s^2, speed {stats(np.linalg.norm(v[m], axis=1))} m/s")
    print(f"  truth lateral specific force          {stats(res_model0)} m/s^2   (model: zero)")
    print(f"  truth lateral after linear drag       {stats(res_model1)} m/s^2   (k {k_iso:+.3f} 1/s)")
    print(f"  path lateral vs truth                 {stats(res_path)} m/s^2")
    print(f"  path along thrust vs truth            {stats(res_ax)} m/s^2   (path/accelerometer scale {slope:.3f})")
    print(f"  as position at this band: path lateral p50 {mm(np.percentile(res_path,50)):.0f} mm, drag model p50 {mm(np.percentile(res_model1,50)):.0f} mm, zero model p50 {mm(np.percentile(res_model0,50)):.0f} mm")
    print(f"  thrust axis from the path alone is {ang:.1f} deg from the accelerometer's")
