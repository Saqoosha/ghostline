"""night_heats.py: the night rebuild of a race day's heats for the audience replay (viewer heat.html): per heat, each pilot's
DVR clip cut from the venue's 2x2 recording, and their path on a 30 fps grid, as heats/<id>/race.json + <cell>.mp4.
Run on the venue box (rt4090) after the day; publish with tools/publish-event.sh <event> --night <out>.
  python night_heats.py <YYYYMMDD> <out> [--answers <rt_replay_day.sh output>] [--relay live/relay] [--rec rec] [--nvenc]
The heats are rt_relay.py's (<relay>/heats-<date>.jsonl: start, end, names). A heat's clip is the recording (rt_rec.py:
rec/<date>-*.mp4 and its .json - "start" the wall clock of its first frame, fps, frames) over [start - 3 s, end + 2 s], one
quarter per cell in the grid's order (tl, tr, bl, br); the clip's first frame is the replay's t = 0.
The path: the recording replayed through the tracker (rt_replay_day.sh: <answers>/<recording stem>/<cell>.jsonl) when there
is one - its frame i is the recording's frame i + SHIFT (an SRT replay loses the recording's first 120 frames; --shift, per run
from rt_replay_verify.py), so its wall time is start + (i + SHIFT) / fps - else the answers the relay kept live
(<relay>/<date>/<id>.json). Both are smoothed onto the grid as the live page draws them.
A heat without answers from either is left out (its live.json stays the replay); a heat whose clip fails is left out too."""
import argparse, datetime, glob, json, os, shutil, subprocess

WS, GAP, PAD_BEFORE, PAD_AFTER = 0.25, 0.3, 3.0, 2.0    # the fit's half window (s), the longest gap filled (s), the clip's margins (s)
SLOTS = ("tl", "tr", "bl", "br")
JST = datetime.timezone(datetime.timedelta(hours=9))
SHIFT = 120                                           # CLAUDE.md: 120-121 frames on all eight evening replays of 2026-10-17


def replay_time(rec, i, shift=SHIFT):                # a replayed answer's frame -> wall seconds
    return rec["start"] + (i + shift) / rec["fps"]


def read_jsonl(f):                                   # a crash can leave a truncated last line: skipped, not fatal
    out = []
    for n, l in enumerate(open(f), 1):
        try: out.append(json.loads(l))
        except ValueError: print(f"{f}:{n}: unreadable line skipped")
    return out


def clip_window(rec, t0, t1):
    """[t0, t1] (wall seconds) as seconds into the recording, clipped to it; None when they do not meet"""
    a, b = max(0.0, t0 - rec["start"]), min(rec["frames"] / rec["fps"], t1 - rec["start"])
    return (a, b) if b > a else None


def _fit(pts, t):                                    # weighted quadratic through (time, pos) at t, biweight weights over WS
    s = [0.0] * 5; b = [[0.0] * 3 for _ in range(3)]; n = 0
    for tt, p in pts:
        x = tt - t; u = x / (WS * 1.0001)
        if abs(u) >= 1: continue
        w = (1 - u * u) ** 2; n += 1
        for k in range(5): s[k] += w * x ** k
        for k in range(3):
            for d in range(3): b[k][d] += w * x ** k * p[d]
    if n < 4: return None
    det = s[0] * (s[2] * s[4] - s[3] ** 2) - s[1] * (s[1] * s[4] - s[3] * s[2]) + s[2] * (s[1] * s[3] - s[2] ** 2)
    if abs(det) < 1e-12: return None
    return [(b[0][d] * (s[2] * s[4] - s[3] ** 2) - s[1] * (b[1][d] * s[4] - s[3] * b[2][d]) + s[2] * (b[1][d] * s[3] - s[2] * b[2][d])) / det for d in range(3)]


def grid(ans, t0, t1, fps=30):
    """answers [(wall s, [x, y, z])] on frames t0 + k / fps up to t1; None where the answers around are more than GAP apart, and outside them"""
    ans = sorted(ans); n = int(round((t1 - t0) * fps)) + 1; out = [None] * n; lo = hi = 0; prev = -1
    for k in range(n):
        t = t0 + k / fps
        while lo < len(ans) and ans[lo][0] < t - WS: lo += 1
        while hi < len(ans) and ans[hi][0] <= t + WS: hi += 1
        while prev + 1 < len(ans) and ans[prev + 1][0] <= t + 1e-9: prev += 1
        at = ans[prev] if prev >= 0 else None; nxt = ans[prev + 1] if prev + 1 < len(ans) else None
        if at is None or (abs(at[0] - t) > 1e-9 and (nxt is None or nxt[0] - at[0] > GAP)): continue
        p = _fit(ans[lo:hi], t) or at[1]
        out[k] = [round(v, 3) for v in p]
    return out


def heats_of(rows):
    """the relay's log lines -> the heats that ended: start, end and their last names"""
    hs, out = {}, []
    for r in rows:
        if r["k"] == "h": hs[r["id"]] = dict(hs.get(r["id"], {}), id=r["id"], start=r["t"], heat=r.get("heat"), cls=r.get("cls"), cells=r.get("cells") or {}, order=r["order"])
        elif r["k"] == "e" and r["id"] in hs and all(o["id"] != r["id"] for o in out): out.append(dict(hs[r["id"]], end=r["t"]))
    return out


def recordings(rec_dir, date, now=None):
    """the day's recordings. One still being written is used only with now (the relay right after a heat): rt_rec.py keeps
    its file to the wall clock, so it holds up to when the mp4 was last written (not now: a recorder killed mid-file leaves
    live=True for good), less the last fragment (2 s, and 1 s of margin) still in the encoder"""
    out = []
    for f in sorted(glob.glob(f"{rec_dir}/{date}-*.json")):
        mp4 = f[:-5] + ".mp4"
        try: r = json.load(open(f)); written = os.path.getmtime(mp4)
        except (ValueError, OSError) as e: print(f"{os.path.basename(f)}: skipped ({e})"); continue
        if r.get("live"):
            if now is None or not r.get("fps") or "start" not in r: continue
            r = dict(r, frames=int(max(0.0, min(now, written) - r["start"] - 3.0) * r["fps"]))
        out.append(dict(r, mp4=mp4, stem=os.path.basename(f)[:-5]))
    return out


def cut_heat(h, recs, out, relay="live/relay", answers=None, fps=30, nvenc=False, shift=SHIFT, log=print):
    """one heat (heats_of's) -> <out>/<id>/race.json and <cell>.mp4; the folder, or None when it has no recording or no answers.
    A clip that fails (ffmpeg's error or timeout) raises: worth trying again, unlike a heat with nothing to cut"""
    t0, t1 = h["start"] / 1000 - PAD_BEFORE, h["end"] / 1000 + PAD_AFTER
    rec = max(recs, key=lambda r: (lambda w: w[1] - w[0] if w else 0)(clip_window(r, t0, t1)), default=None)
    w = rec and clip_window(rec, t0, t1)
    if not w: log(f"{h['id']}: no recording"); return None
    c0, c1 = rec["start"] + w[0], rec["start"] + w[1]; W, H = rec["size"]; d = f"{out}/{h['id']}"; os.makedirs(d, exist_ok=True)
    live = None; pilots = []
    for k, s in enumerate(h["order"]):
        src = f"{answers}/{rec['stem']}/{s}.jsonl" if answers else None
        if src and os.path.exists(src):
            ans = [(replay_time(rec, x["i"], shift), x["pos"]) for x in read_jsonl(src) if "pos" in x]; how = "replay"
        else:
            if live is None:
                f = f"{relay}/{day_of(h)}/{h['id']}.json"; live = json.load(open(f))["answers"] if os.path.exists(f) else {}
            ans = [(x[0] / 1000, x[1:4]) for x in live.get(s, [])]; how = "live"
        ans = [x for x in ans if c0 - 1 <= x[0] <= c1 + 1]
        if len(ans) < 30: log(f"{h['id']} {s}: {len(ans)} answers ({how}), left out"); continue
        x, y = k % 2 * (W // 2), k // 2 * (H // 2)
        # 30 fps, about 1.5 Mbit/s: the 60 fps quarter at the encoder's defaults was 4.6 Mbit/s (71-89 MB for 155 s), too much for a phone
        enc = (["-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "30", "-b:v", "1500k", "-maxrate", "2000k", "-bufsize", "4000k"] if nvenc
               else ["-c:v", "libx264", "-crf", "28", "-preset", "veryfast", "-maxrate", "2000k", "-bufsize", "4000k"]) + ["-r", "30"]
        # 4 threads: on the venue box this runs while the next heat's tracker starts
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-threads", "4", "-ss", f"{w[0]:.3f}", "-i", rec["mp4"], "-t", f"{w[1] - w[0]:.3f}", "-vf", f"crop={W // 2}:{H // 2}:{x}:{y}",
                            "-an", *enc, "-threads", "4", "-pix_fmt", "yuv420p", "-movflags", "+faststart", f"{d}/{s}.mp4"], capture_output=True, text=True, timeout=600)
        if r.returncode: shutil.rmtree(d, ignore_errors=True); raise RuntimeError(f"{h['id']} {s}: ffmpeg failed: {r.stderr.strip()[-300:]}")
        pilots.append(dict(stream=s, name=h["cells"].get(s, ""), pos=grid(ans, c0, c1, fps), video=f"{s}.mp4", source=how))
        log(f"{h['id']} {s}: {how}, {len(ans)} answers, {SLOTS[k]}")
    if not pilots: shutil.rmtree(d, ignore_errors=True); log(f"{h['id']}: left out"); return None
    with open(f"{d}/race.json.tmp", "w") as o:    # whole or not at all: the relay reads its presence as "cut"
        json.dump(dict(id=h["id"], start=round(c0 * 1000), end=round(c1 * 1000), heat=h["heat"], cls=h["cls"], fps=fps, version="night", order=h["order"],
                       recording=rec["stem"], pilots=pilots), o, separators=(",", ":"), ensure_ascii=False)
    os.replace(f"{d}/race.json.tmp", f"{d}/race.json")
    return d


def day_of(h): return datetime.datetime.fromtimestamp(h["start"] / 1000, JST).strftime("%Y%m%d")   # the relay's folder for the heat


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("date"); ap.add_argument("out"); ap.add_argument("--answers"); ap.add_argument("--relay", default="live/relay")
    ap.add_argument("--rec", default="rec"); ap.add_argument("--fps", type=int, default=30); ap.add_argument("--nvenc", action="store_true")
    ap.add_argument("--shift", type=int, default=SHIFT)
    a = ap.parse_args()
    recs = recordings(a.rec, a.date)
    for h in heats_of(read_jsonl(f"{a.relay}/heats-{a.date}.jsonl")):
        try: cut_heat(h, recs, a.out, a.relay, a.answers, a.fps, a.nvenc, a.shift)
        except (RuntimeError, subprocess.TimeoutExpired) as e: print(f"{h['id']}: left out - {e}")


if __name__ == "__main__": main()
