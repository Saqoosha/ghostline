"""night_heats.py: the night rebuild of a race day's heats for the audience replay (viewer heat.html): per heat, each pilot's
DVR clip cut from the venue's 2x2 recording, and their path on a 30 fps grid, as heats/<id>/race.json + <cell>.mp4.
Run on the venue box (rt4090) after the day; publish with tools/publish-event.sh <event> --night <out>.
  python night_heats.py <YYYYMMDD> <out> [--answers <rt_replay_day.sh output>] [--relay live/relay] [--rec rec] [--nvenc]
The heats are rt_relay.py's (<relay>/heats-<date>.jsonl: start, end, names). A heat's clip is the recording (rt_rec.py:
rec/<date>-*.mp4 and its .json - "start" the wall clock of its first frame, fps, frames) over [start - 3 s, end + 2 s], one
quarter per cell in the grid's order (tl, tr, bl, br); the clip's first frame is the replay's t = 0.
The path: the recording replayed through the tracker (rt_replay_day.sh: <answers>/<recording stem>/<cell>.jsonl) when there
is one - its frame i is the recording's frame i (ffmpeg -fps_mode passthrough; measured 2026-10-18 on 20261017-164422: 60.0
frames a second of the tracker's clock in every cell, the last i inside the file), so its wall time is start + i / fps - else
the answers the relay kept live (<relay>/<date>/<id>.json). Both are smoothed onto the grid as the live page draws them."""
import argparse, glob, json, os, subprocess

WS, GAP, PAD_BEFORE, PAD_AFTER = 0.25, 0.3, 3.0, 2.0    # the fit's half window (s), the longest gap filled (s), the clip's margins (s)
SLOTS = ("tl", "tr", "bl", "br")


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
    """answers [(wall s, [x, y, z])] on frames t0 + k / fps up to t1; a frame further than GAP from an answer on a side is None"""
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
        elif r["k"] == "e" and r["id"] in hs: out.append(dict(hs[r["id"]], end=r["t"]))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("date"); ap.add_argument("out"); ap.add_argument("--answers"); ap.add_argument("--relay", default="live/relay")
    ap.add_argument("--rec", default="rec"); ap.add_argument("--fps", type=int, default=30); ap.add_argument("--nvenc", action="store_true")
    a = ap.parse_args()
    heats = heats_of([json.loads(l) for l in open(f"{a.relay}/heats-{a.date}.jsonl")])
    recs = []
    for f in sorted(glob.glob(f"{a.rec}/{a.date}-*.json")):
        r = json.load(open(f))
        if not r.get("live") and os.path.exists(f[:-5] + ".mp4"): recs.append(dict(r, mp4=f[:-5] + ".mp4", stem=os.path.basename(f)[:-5]))
    for h in heats:
        t0, t1 = h["start"] / 1000 - PAD_BEFORE, h["end"] / 1000 + PAD_AFTER
        rec = max(recs, key=lambda r: (lambda w: w[1] - w[0] if w else 0)(clip_window(r, t0, t1)), default=None)
        w = rec and clip_window(rec, t0, t1)
        if not w: print(f"{h['id']}: no recording"); continue
        c0, c1 = rec["start"] + w[0], rec["start"] + w[1]; W, H = rec["size"]; d = f"{a.out}/{h['id']}"; os.makedirs(d, exist_ok=True)
        live = None; pilots = []
        for k, s in enumerate(h["order"]):
            src = f"{a.answers}/{rec['stem']}/{s}.jsonl" if a.answers else None
            if src and os.path.exists(src):
                ans = [(rec["start"] + x["i"] / rec["fps"], x["pos"]) for x in map(json.loads, open(src)) if "pos" in x]; how = "replay"
            else:
                if live is None:
                    f = f"{a.relay}/{a.date}/{h['id']}.json"; live = json.load(open(f))["answers"] if os.path.exists(f) else {}
                ans = [(x[0] / 1000, x[1:4]) for x in live.get(s, [])]; how = "live"
            ans = [x for x in ans if c0 - 1 <= x[0] <= c1 + 1]
            if len(ans) < 30: continue                    # this cell had nobody flying
            x, y = k % 2 * (W // 2), k // 2 * (H // 2)
            # 30 fps, about 1.5 Mbit/s: the 60 fps quarter at the encoder's defaults was 4.6 Mbit/s (71-89 MB for 155 s), too much for a phone
            enc = (["-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "30", "-b:v", "1500k", "-maxrate", "2000k", "-bufsize", "4000k"] if a.nvenc
                   else ["-c:v", "libx264", "-crf", "28", "-preset", "veryfast", "-maxrate", "2000k", "-bufsize", "4000k"]) + ["-r", "30"]
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{w[0]:.3f}", "-i", rec["mp4"], "-t", f"{w[1] - w[0]:.3f}", "-vf", f"crop={W // 2}:{H // 2}:{x}:{y}",
                            "-an", *enc, "-pix_fmt", "yuv420p", "-movflags", "+faststart", f"{d}/{s}.mp4"], check=True)
            pilots.append(dict(stream=s, name=h["cells"].get(s, ""), pos=grid(ans, c0, c1, a.fps), video=f"{s}.mp4", source=how))
            print(f"{h['id']} {s}: {how}, {len(ans)} answers, {SLOTS[k]}")
        json.dump(dict(id=h["id"], start=round(c0 * 1000), end=round(c1 * 1000), heat=h["heat"], cls=h["cls"], fps=a.fps, version="night", order=h["order"],
                       recording=rec["stem"], pilots=pilots), open(f"{d}/race.json", "w"), separators=(",", ":"), ensure_ascii=False)


if __name__ == "__main__": main()
