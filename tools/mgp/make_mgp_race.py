"""Build one race-page data folder (race.json + per-pilot DVR clips + audio) for a 2026 MultiGP Championship qualifier round.
Same schema as tools/dvr/make_race.py's race.json, plus pilot.country / official_laps / position / video_size / slot.

  make_mgp_race.py build --round 1 --poses build/mgp/poses/q01 --out build/mgp/q01 [--slots TBELL=0,FORMULA=6] [--clips]
  make_mgp_race.py from-top --top top.json --fit fit.json --names r01_s0=TBELL,... --out build/mgp/poses/q01
      (dev adapter: the SfM 3 fps ground-plane paths -> the per-pilot pose files below)

POSE FILES (the contract the final localizer replaces): <poses>/<PILOT>.json, PILOT as in livefpv_laps.json
  {"pilot": "TBELL", "slot": 0, "frames": [{"t_ms": 12345, "center": [x, y, z], "qvec": [qx, qy, qz, qw] | null}, ...]}
  t_ms  = milliseconds in the 8-up recording file (video time, from the first frame); frames sorted by time, any rate.
  center = camera centre in the page's WORLD FRAME: metres, y up (floor = 0), x = VelociDrone x, z = -VelociDrone z.
  qvec  = camera-to-world rotation (OpenCV camera axes x right, y down, z forward), world axes as above. The builder
          does not need it yet (race.json holds positions only); it is kept in the contract for the viewer.
Poses are linearly interpolated to the race fps. Before the first / after the last frame the path holds its end point;
pilot.end is the clip time of the last frame.

CLIP TIME: clip frame i is video time clip_t0 + i / fps; race.json.start is the horn in clip time. Every pilot's clip is cut
from the same window [clip_t0, clip_t1] of the recording, so all clips and pos arrays share one timeline.
OFFICIAL LAPS (LiveFPV): lap 1 is timed from the pilot's own HOLESHOT crossing of the start gate (the lapNum-0 row, or the
first row when the transponder missed it: same meaning, checked against Round 1 paths, offsets constant within 0.1 s),
so total = sum of laps and race.json laps[0] = lap_ends[0] - holeshot_at, not - start.
"""
import argparse, glob, json, os, re, subprocess, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import vd_track

ROOT = os.path.dirname(os.path.dirname(HERE))
TRACK_TXT = f"{ROOT}/build/mgp/champs2026.txt"      # decrypted VelociDrone track (Velbd output)
COLORS = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#00c7be", "#0a84ff", "#af52de", "#ff2d92"]
HOLE_LEAD = 2.0      # assumed horn -> first start-gate pass (FDF holeshots were 1.6-2.4 s), used only when no start is given
RESTART_GAP = 120.0  # no qualifier lap is this long: a gap like it between passes means two heats in one recording
PRE = 4.0           # clip pre-roll before the horn (page opens ~2 s + audio lead before the start)
POST = 6.0
CELL_W, CELL_H, FEED_X, FEED_W = 640, 360, 80, 480


# ---------------------------------------------------------------- poses
def slug(n): return re.sub(r"[^a-z0-9]", "", n.lower())


def load_poses(d, names):
    out = {}
    for n in names:
        p = f"{d}/{n}.json"
        if os.path.exists(p):
            J = json.load(open(p)); F = J["frames"]
            t = np.array([f["t_ms"] for f in F], float) / 1000; c = np.array([f["center"] for f in F], float)
            q = np.array([f["qvec"] if f.get("qvec") else [np.nan] * 4 for f in F], float)
            o = np.argsort(t); out[n] = dict(t=t[o], c=c[o], q=q[o], slot=J.get("slot"), run=J.get("run"))
    return out


def resample(P, t0, fps, n):
    """pos[i] at video time t0 + i/fps (linear, holds the ends); n frames."""
    T = t0 + np.arange(n) / fps
    return np.c_[[np.interp(T, P["t"], P["c"][:, d]) for d in range(3)]].T


def from_top(a):
    top = json.load(open(a.top)); fit = json.load(open(a.fit)); R = np.array(fit["R"]); sc = fit["scale"]; t = np.array(fit["t"]); m = fit["mirror"]
    os.makedirs(a.out, exist_ok=True)
    for pair in a.names.split(","):
        key, name = pair.split("="); A = np.array(top[key]); slot = int(re.search(r"_s(\d+)", key).group(1))
        vd = (np.c_[A[:, 1], m * A[:, 2]] @ R.T) * sc + t            # [x, mirror*y] -> VelociDrone [x, z]
        C = np.c_[vd[:, 0], A[:, 3] * sc, -vd[:, 1]]                  # world: z flipped, y = h * scale
        v = np.gradient(C[:, [0, 2]], A[:, 0] / 1000, axis=0); v = np.apply_along_axis(lambda c: np.convolve(np.pad(c, 3, mode="edge"), np.ones(7) / 7, "valid"), 0, v)
        frames = []
        for i in range(len(A)):
            f = np.array([v[i, 0], 0, v[i, 1]]); f = f / np.linalg.norm(f) if np.linalg.norm(f) > 0.5 else np.array([0, 0, 1.0])
            dn = np.array([0, -1.0, 0]); rx = np.cross(dn, f); Rm = np.c_[rx, dn, f]   # columns = camera x, y, z in world
            frames.append({"t_ms": int(A[i, 0]), "center": np.round(C[i], 3).tolist(), "qvec": np.round(_quat(Rm), 4).tolist()})
        json.dump({"pilot": name, "slot": slot, "qvec_source": "heading-from-velocity (placeholder)", "frames": frames}, open(f"{a.out}/{name}.json", "w"))
        print(name, slot, len(frames), "frames")


def _quat(R):
    t = np.trace(R)
    if t > 0:
        s = np.sqrt(t + 1) * 2; return np.array([(R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, s / 4])
    i = int(np.argmax(np.diag(R))); j, k = (i + 1) % 3, (i + 2) % 3
    s = np.sqrt(R[i, i] - R[j, j] - R[k, k] + 1) * 2; q = np.zeros(4)
    q[i] = s / 4; q[j] = (R[j, i] + R[i, j]) / s; q[k] = (R[k, i] + R[i, k]) / s; q[3] = (R[k, j] - R[j, k]) / s
    return q


# ---------------------------------------------------------------- laps
def crossings(pos, fps, gate, margin, min_gap):
    """Forward passes of the start/finish plane (travel direction = gate normal), within width + margin and height + 2 m.
    Backward and wobble passes within min_gap s of the previous kept one are dropped. Clip times (s)."""
    n = np.array(gate["normal"])[[0, 2]]; w = np.array(gate["width_dir"])[[0, 2]]; c = np.array(gate["centre"])
    rel = pos - c; s = rel[:, [0, 2]] @ n; lat = rel[:, [0, 2]] @ w; h = rel[:, 1]
    out = []
    for i in range(len(pos) - 1):
        if s[i] < 0 <= s[i + 1] and abs(lat[i]) < gate["half_width"] + margin and -1 < h[i] < gate["height"] + 2:
            tc = (i + s[i] / (s[i] - s[i + 1])) / fps
            if not out or tc - out[-1] >= min_gap: out.append(tc)
    return out


def fmt_time(t):
    return f"{int(t // 60)}:{t % 60:06.3f}" if t >= 60 else f"{t:.3f}"


def official_for(rnd):
    R = next(r for r in json.load(open(f"{ROOT}/data/mgp/livefpv_laps.json")) if r["round"] == rnd)
    return R, {p["pilot"]: [t for n, t in p["laps"] if n > 0] for p in R["pilots"]}


# ---------------------------------------------------------------- slots / recording
def video_file(rnd, aliases):
    hits = sorted(f for f, a in aliases.items() if re.search(rf"Race {rnd} ", a + " "))
    return hits[0] if hits else None


def slots_from_ocr(ocr_path, names):
    """slot -> official pilot name: the name token that shows most often in a cell's overlay bar (needs the overlay, i.e. later rounds)."""
    D = json.load(open(ocr_path)); cnt = {}
    for r in D["rows"]:
        for s, toks in r["cells"].items():
            for tk in toks:
                tk = re.sub(r"\s*LAP\s*\d+$", "", tk.strip()).replace(" ", "")
                for n in names:
                    if tk.upper() == n.upper(): cnt.setdefault(int(s), {}).setdefault(n, 0); cnt[int(s)][n] += 1
    return {max(v, key=v.get): s for s, v in cnt.items()}


# ---------------------------------------------------------------- track line (as make_race.py)
def _resamp(pts, m):
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]; u = np.linspace(0, s[-1], m)
    return np.c_[[np.interp(u, s, pts[:, d]) for d in range(3)]].T


def _dtw(A, B, band):
    n_, m = len(A), len(B); D = np.full((n_ + 1, m + 1), np.inf); D[0, 0] = 0
    for i in range(1, n_ + 1):
        cc = int(i * m / n_); lo = max(1, cc - band); hi = min(m, cc + band); d = np.linalg.norm(B[lo - 1:hi] - A[i - 1], axis=1)
        for jj, j in enumerate(range(lo, hi + 1)): D[i, j] = d[jj] + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    i, j = n_, m; pairs = []
    while i > 0 and j > 0:
        pairs.append((i - 1, j - 1)); k_ = np.argmin([D[i - 1, j - 1], D[i - 1, j], D[i, j - 1]])
        if k_ == 0: i, j = i - 1, j - 1
        elif k_ == 1: i -= 1
        else: j -= 1
    return pairs[::-1]


def _smooth(x, sig):
    r = int(3 * sig); ww = np.exp(-0.5 * (np.arange(-r, r + 1) / sig) ** 2); ww /= ww.sum()
    xp = np.r_[np.repeat(x[:1], r, 0), x, np.repeat(x[-1:], r, 0)]
    return np.c_[[np.convolve(xp[:, d], ww, "valid") for d in range(3)]].T


def track_line(pos, bounds, fps, N=1600):
    """Mean of the laps between consecutive bounds (clip times) by DTW, three rounds, 0.5 m smoothing; None with < 2 laps."""
    if len(bounds) < 3: return None
    at = lambda t: (lambda f, i: pos[min(i, len(pos) - 1)] * (1 - (f - i)) + pos[min(i + 1, len(pos) - 1)] * (f - i))(t * fps, int(t * fps))
    laps = [_resamp(np.array([at(t) for t in np.linspace(a, b, max(int((b - a) * fps * 8), 8))]), 2 * N) for a, b in zip(bounds[:-1], bounds[1:])]
    mean = laps[1 % len(laps)][::2].copy()
    for _ in range(3):
        per = []
        for Lp in laps:
            acc = [[] for _ in range(N)]
            for i, j in _dtw(mean, Lp, 320): acc[i].append(Lp[j])
            per.append(np.array([np.mean(x, axis=0) for x in acc]))
        mean = _resamp(_smooth(np.mean(per, axis=0), 2), N)
    return mean


# ---------------------------------------------------------------- build
def build(a):
    rnd = a.round; fps = a.fps
    if a.gates:                                                       # gates found in the scan (gates_from_map.py); VD is only the prior
        G = json.load(open(a.gates)); gate, cps, tname = G["gate"], G["checkpoints"], G["track_name"]
    else:
        T = vd_track.load(a.track); gate, cps, tname = vd_track.finish_gate(T), T["checkpoints"], T["name"]
    R, off = official_for(rnd); names = [p["pilot"] for p in R["pilots"]]
    P = load_poses(a.poses, names)
    if not P: sys.exit(f"no pose files for round {rnd} in {a.poses}")
    aliases = json.loads(subprocess.check_output(["ssh", "studio", "cat ~/mgp/aliases.json"]))
    vfile = a.video_file or video_file(rnd, aliases)
    dur = float(subprocess.check_output(["ssh", "studio", f"ffprobe -v error -show_entries format=duration -of csv=p=0 ~/mgp/raw/{vfile}"]))

    # slot per pilot: --slots > the pose file's slot > the overlay OCR
    slots = {k: int(v) for k, v in (kv.split("=") for kv in a.slots.split(","))} if a.slots else {}
    for n, p in P.items():
        if n not in slots and p["slot"] is not None: slots[n] = int(p["slot"])
    ocr = f"{a.ocr_dir}/{vfile}.json"
    ocr_slots = slots_from_ocr(ocr, names) if os.path.exists(ocr) else {}
    for n, s in ocr_slots.items(): slots.setdefault(n, s)
    has_overlay = bool(ocr_slots)
    crop_top = a.crop_top if a.crop_top is not None else (40 if has_overlay else 0)

    # holeshots in video time, from the poses alone (3 s grid: crossings from a long pre-roll resample)
    t_lo = min(p["t"][0] for p in P.values()); t_hi = max(p["t"][-1] for p in P.values())
    nfull = int((t_hi - t_lo) * fps) + 1
    cross_v = {n: [t_lo + c for c in crossings(resample(p, t_lo, fps, nfull), fps, gate, a.margin, a.min_gap)] for n, p in P.items()}
    # a recording with two heats in it (a restart, or the next round recorded on): name_slots.py wrote the run each pilot's
    # official laps matched in; only passes in that window count when there was a gap of minutes between passes
    runs = [p["run"] for p in P.values() if p.get("run")]
    two = any(c[i + 1] - c[i] > RESTART_GAP for c in cross_v.values() for i in range(len(c) - 1))
    if runs and two and a.start_video is None:
        lo, hi = min(r[0] for r in runs) - 15.0, max(r[1] for r in runs) + 15.0
        cross_v = {n: [x for x in c if lo <= x <= hi] for n, c in cross_v.items()}
        print(f"round {rnd}: two heats in the recording, using the passes in [{lo:.1f}, {hi:.1f}] s")
    holes = {n: c[0] for n, c in cross_v.items() if c}
    start_v = a.start_video if a.start_video is not None else min(holes.values()) - HOLE_LEAD
    L = max(len(v) for v in off.values()) or 1
    clip_t0 = max(0.0, start_v - PRE)
    arrive = max([c[min(len(c) - 1, L)] for c in cross_v.values() if c] + [start_v + 30])   # last lap end, or the last pass of a pilot who did fewer
    clip_t1 = min(dur, arrive + POST); nfr = int((clip_t1 - clip_t0) * fps) + 1
    start = round(start_v - clip_t0, 3)

    race = dict(title=f"MultiGP Championship 2026 · Qualifier Round {rnd}", round=rnd, fps=fps, start=start, scene=a.scene,
                laps=L, gate=gate, checkpoints=cps, track_name=tname, pilots=[], source=dict(
                    recording=vfile, clip_t0=round(clip_t0, 3), clip_t1=round(clip_t1, 3), start_basis=("given" if a.start_video is not None else f"min holeshot - {HOLE_LEAD} s (assumption)")))
    cmp_ = {}
    ranked = sorted(names, key=lambda n: (-len(off[n]), (cross_v[n][len(off[n])] if n in cross_v and len(cross_v[n]) > len(off[n]) else (holes.get(n, 0) + sum(off[n])))))
    pos_of = {n: i + 1 for i, n in enumerate(ranked)}
    lead_t = sum(off[ranked[0]]); countries = json.load(open(a.countries)) if a.countries and os.path.exists(a.countries) else {}
    for n in names:
        if n not in P or n not in slots: continue
        pos = resample(P[n], clip_t0, fps, nfr); pr = np.round(pos, 2)
        cr = crossings(pr, fps, gate, a.margin, a.min_gap)
        hs = float(cr[0]) if cr else None; ends = [float(x) for x in cr[1:1 + L]]; ol = off[n]
        laps = [round(float(b - a_), 3) for a_, b in zip(([hs] if hs is not None else []) + ends[:-1], ends)] if hs is not None else []
        end = round(min(float(P[n]["t"][-1] - clip_t0), (nfr - 1) / fps), 3); fin = ends[L - 1] if len(ends) >= L else None
        k = len(ol); tot = round(sum(ol), 3)
        cp = [round(float(x - y), 3) for x, y in zip(laps, ol)]
        cmp_[n] = dict(official=ol, path=laps, diff=cp, official_total=tot, path_total=round(sum(laps), 3), n_official=k, n_path=len(laps),
                       holeshot_video=round(hs + clip_t0, 3) if hs is not None else None)
        gap = "" if pos_of[n] == 1 else (f"+{sum(ol) - lead_t:.3f}" if k == len(off[ranked[0]]) else f"-{len(off[ranked[0]]) - k}L")
        race["pilots"].append(dict(
            name=n, color=COLORS[len(race["pilots"]) % 8], end=end, crashed=k < L, finish=fin, lap_ends=[round(x, 3) for x in ends], laps=laps,
            total=round(ends[-1] - hs, 3) if hs is not None and ends else 0.0, official=dict(pos=pos_of[n], laps=k, time=fmt_time(tot), gap=gap),
            pos=pr.tolist(), video=f"dvr_{slug(n)}.mp4", video_size=[FEED_W, CELL_H - crop_top], holeshot_at=round(hs, 3) if hs is not None else None,
            holeshot=round(hs - start, 3) if hs is not None else None, slot=slots[n], official_laps=ol, position=pos_of[n],
            **({"country": countries[n]} if n in countries else {})))
    # track line from the pilot with the most path laps
    best = max(race["pilots"], key=lambda p: len(p["lap_ends"]), default=None)
    if best and len(best["lap_ends"]) >= 2:
        line = track_line(np.array(best["pos"]), [best["holeshot_at"]] + best["lap_ends"], fps)
        race["track"] = np.round(line, 2).tolist(); race["track_source"] = f"DTW mean of {best['name']}'s {len(best['lap_ends'])} path laps"
    else:
        cp = np.array([g["centre"] for g in cps if g["prefab"] in vd_track.RF_GATES]); race["track"] = np.round(_resamp(np.r_[cp, cp[:1]], 1600), 2).tolist()
        race["track_source"] = "gate centres in checkpoint order (no pilot with 2 laps)"
    os.makedirs(a.out, exist_ok=True)
    json.dump(race, open(f"{a.out}/race.json", "w"), separators=(",", ":"))
    json.dump(dict(round=rnd, start_video=round(start_v, 3), compare=cmp_, slots=slots), open(f"{a.out}/compare.json", "w"), indent=1)
    print(f"round {rnd}: recording {vfile} ({dur:.1f} s), clip [{clip_t0:.2f}, {clip_t1:.2f}], start {start} (video {start_v:.2f}), {L} laps")
    for n, c in cmp_.items():
        print(f"  {n}: official {c['official']} total {c['official_total']}  path {c['path']} total {c['path_total']}  diff {c['diff']}")
    if a.clips: make_clips(a, race, vfile, clip_t0, clip_t1, crop_top)


def make_clips(a, race, vfile, t0, t1, crop_top):
    """One ffmpeg pass on the studio (nice 10): a cropped h264 clip per pilot + the race audio, then scp back."""
    ps = race["pilots"]; rd = f"/tmp/mgp_q{race['round']:02d}"
    maps = ""; fc = f"[0:v]split={len(ps)}" + "".join(f"[v{i}]" for i in range(len(ps))) + ";"
    for i, p in enumerate(ps):
        r, c = divmod(p["slot"], 3)
        fc += f"[v{i}]crop={FEED_W}:{CELL_H - crop_top}:{c * CELL_W + FEED_X}:{r * CELL_H + crop_top}[o{i}];"
        maps += f" -map '[o{i}]' -c:v libx264 -preset veryfast -crf 24 -pix_fmt yuv420p -an -movflags +faststart {rd}/{p['video']}"
    cmd = (f"mkdir -p {rd} && nice -n 10 ffmpeg -y -loglevel error -ss {t0:.3f} -t {t1 - t0:.3f} -i ~/mgp/raw/{vfile} "
           f"-filter_complex \"{fc.rstrip(';')}\"{maps} -map '0:a?' -c:a aac -b:a 96k -vn {rd}/audio.m4a")
    print(cmd); subprocess.check_call(["ssh", "studio", cmd])
    subprocess.check_call(["rsync", "-a", f"studio:{rd}/", f"{a.out}/"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build"); b.add_argument("--round", type=int, required=True); b.add_argument("--poses", required=True); b.add_argument("--out", required=True)
    b.add_argument("--track", default=TRACK_TXT); b.add_argument("--fps", type=int, default=30); b.add_argument("--scene", default="MGP-Champs-2026")
    b.add_argument("--slots", help="PILOT=slot,... (overrides pose file slots and OCR)"); b.add_argument("--video-file"); b.add_argument("--ocr-dir", default=os.path.expanduser("~/mgp/ocr"))
    b.add_argument("--start-video", type=float, help="horn in recording time (s); default min holeshot - 2 s")
    b.add_argument("--margin", type=float, default=3.0, help="lateral slack (m) around the gate for a pass; 3 fps SfM paths need more than 1.5")
    b.add_argument("--min-gap", type=float, default=6.0, help="drop passes closer than this (s) to the previous one"); b.add_argument("--crop-top", type=int, help="px cut from the top of each feed (overlay bar); default 40 if the round has the overlay")
    b.add_argument("--countries", help="JSON {pilot: ISO-2}"); b.add_argument("--clips", action="store_true")
    b.add_argument("--gates", help="gates.json from gates_from_map.py (gate poses measured in the scan) instead of the VD track")
    f = sp.add_parser("from-top"); f.add_argument("--top", required=True); f.add_argument("--fit", required=True); f.add_argument("--names", required=True); f.add_argument("--out", required=True)
    a = ap.parse_args()
    from_top(a) if a.cmd == "from-top" else build(a)
