"""Find the real gates in the trained 3DGS of a round (to_metric.py's track frame: x = VelociDrone x, y = VelociDrone z,
z = up) and write gates.json in the race page's WORLD FRAME (x = VD x, y = up, z = -VD z), same gate dicts as vd_track.py.
The VelociDrone track is a prior only: it gives the gate count, order and roughly where to look; positions, directions
and sizes come from the splats.
usage: gates_from_map.py <metric.ply> <track.txt> <out_dir> [--opacity 0.2] [--eps 0.3] [--crops 7,23,27]
  -> <out_dir>/gates.json, gates-top.png, gates-crop-<order>.png      (VERBOSE=1 prints every line and upright pair)

Method: ground = plane through the densest low layer of opaque splats (the grass). Opaque, non-grass splats 0.3..5 m
above it are clustered in the horizontal plane (single linkage, eps); each cluster is cut into straight lines (RANSAC),
and along each line the uprights are where splats stack vertically (many height bins occupied at one spot: a tube or a
leg, not a banner). Two uprights 2..3.3 m apart make a frame when the space between them is empty at mid height and
closed by a top bar (the banner: a thick
band of splats, >= 0.25 m tall). A column holds one storey per top bar (stacked frames; a raised frame has a thin bottom
bar below its banner). A flat, square, hollow cluster ~3 m across at 2..3.5 m is a window (prefab 286, flown through
vertically). Prior gates (prefabs 285 / 286 / 283, grouped into columns by XY) are matched one-to-one to detected
columns within 4 m (Hungarian, cost = distance + direction mismatch); unmatched priors keep the VD pose with confidence
0, unmatched detections are listed as "extra". Width check: the distance between the two uprights' centres per frame
("opening"), and that minus the tube width ("inner"); the 7 ft spec would give 2.13 m inner.
"""
import argparse, json, os, sys
import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import vd_track

H_LO, H_HI = 0.3, 5.0             # height band of the candidate splats above the ground plane
UP_BAND = (0.4, 1.6)              # heights where a line's splats are its uprights (and legs)
OPEN = (2.0, 3.3)                 # upright centre-to-centre distance a frame may have (m)
MATCH_R = 4.0                     # prior -> detection search radius (m)
VERBOSE = bool(os.environ.get("VERBOSE"))


def load_ply(path):
    raw = open(path, "rb").read()
    end = raw.index(b"end_header\n") + len(b"end_header\n")
    head = raw[:end].decode()
    props = [l.split()[-1] for l in head.splitlines() if l.startswith("property")]
    n = int([l for l in head.splitlines() if l.startswith("element vertex")][0].split()[-1])
    v = np.frombuffer(raw[end:], np.float32, n * len(props)).reshape(n, len(props))
    ix = {p: i for i, p in enumerate(props)}
    P = v[:, [ix["x"], ix["y"], ix["z"]]].astype(np.float64)
    op = 1 / (1 + np.exp(-v[:, ix["opacity"]].astype(np.float64)))
    rgb = np.clip(0.5 + 0.2820948 * v[:, [ix["f_dc_0"], ix["f_dc_1"], ix["f_dc_2"]]], 0, 1)
    return P, op, rgb


def ground_plane(P, op):
    """z = a x + b y + c through the densest low layer of the opaque splats, refined on its inliers."""
    k = op > 0.3
    h, e = np.histogram(P[k, 2], bins=np.arange(-5, 5, 0.05))
    z0 = e[np.argmax(h)] + 0.025
    sel = k & (np.abs(P[:, 2] - z0) < 0.6)
    for tol in (0.4, 0.3, 0.2, 0.15):
        X = np.c_[P[sel, :2], np.ones(sel.sum())]
        abc = np.linalg.lstsq(X, P[sel, 2], rcond=None)[0]
        sel = k & (np.abs(P[:, 2] - np.c_[P[:, :2], np.ones(len(P))] @ abc) < tol)
    return abc, int(sel.sum())


def clusters(XY, eps, min_pts):
    pairs = cKDTree(XY).query_pairs(eps, output_type="ndarray")
    g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(XY), len(XY)))
    n, lab = connected_components(g, directed=False)
    cnt = np.bincount(lab)
    return [np.flatnonzero(lab == i) for i in range(n) if cnt[i] >= min_pts]


def lines(XY, idx, rng, tol=0.25, grab=0.45, min_pts=25):
    """Straight runs in one cluster: RANSAC the line with most inliers (within tol) among the points not yet on a line,
    then give it every cluster point within grab of it (the rest of the slab, and the corner where two frames touch, which
    both lines keep), and repeat on what is left."""
    rest = idx.copy(); out = []
    while len(rest) >= min_pts:
        Q = XY[rest]; best = (0, None)
        for _ in range(300):
            a, b = Q[rng.choice(len(Q), 2, replace=False)]
            d = b - a
            if np.linalg.norm(d) < 0.5: continue
            u = d / np.linalg.norm(d); nrm = np.array([-u[1], u[0]])
            inl = np.abs((Q - a) @ nrm) < tol
            if inl.sum() > best[0]: best = (int(inl.sum()), (u, a, inl))
        if best[0] < min_pts: break
        u, a, inl = best[1]
        m = Q[inl].mean(0); _, _, Vt = np.linalg.svd(Q[inl] - m, full_matrices=False); u = Vt[0]        # refine the direction
        nrm = np.array([-u[1], u[0]])
        out.append(dict(idx=idx[np.abs((XY[idx] - m) @ nrm) < grab], u=u, nrm=nrm, m=m))
        rest = rest[np.abs((Q - m) @ nrm) >= grab]
    return out


def peaks1d(x, lo, hi, bw, min_cnt, min_sep, rel=0.0):
    """Centres of the maxima of a smoothed histogram of x: >= min_cnt (and >= rel x the strongest), >= min_sep apart."""
    e = np.arange(lo, hi + bw, bw); c = np.histogram(x, bins=e)[0].astype(float)
    if len(c) < 3: return []
    c = np.convolve(c, [0.25, 0.5, 0.25], "same"); out = []
    for i in np.argsort(-c):
        if c[i] < min_cnt or c[i] < rel * c.max(): break
        if all(abs(e[i] - p) > min_sep for p in out): out.append(float(e[i] + bw / 2))
    return sorted(out)


def uprights_on_line(s, hq):
    """Positions along the line where splats stack vertically: per 0.1 m of s, how many 0.2 m height bins between 0.4 and
    4.8 m hold a splat (a banner adds <= 3 everywhere, an upright or leg 5 or more). Peaks >= 3 bins and >= 1.4 x the
    line's median, refined to the median s of the splats in the upright band."""
    e = np.arange(s.min(), s.max() + 0.1, 0.1)
    if len(e) < 4: return []
    hb = np.floor(np.clip(hq, 0.4, 4.8) / 0.2).astype(int); sb = np.clip(np.digitize(s, e) - 1, 0, len(e) - 2)
    use = (hq > 0.4) & (hq < 4.8)
    prof = np.zeros(len(e) - 1)
    for i in np.unique(sb[use]): prof[i] = len(np.unique(hb[use & (sb == i)]))
    prof = np.convolve(prof, [0.25, 0.5, 0.25], "same"); floor = np.median(prof[prof > 0]) if (prof > 0).any() else 0
    out = []
    for i in np.argsort(-prof):
        if prof[i] < 3 or prof[i] < 1.4 * floor: break
        if all(abs(e[i] - p) > 0.9 for p in out): out.append(float(e[i] + 0.05))
    band = (hq > UP_BAND[0]) & (hq < UP_BAND[1]) | (hq > 2.2) & (hq < 4.0)
    return sorted(float(np.median(s[band & (np.abs(s - p) < 0.3)])) if (band & (np.abs(s - p) < 0.3)).sum() >= 3 else p for p in out)


def frames_on_line(P, h, ln):
    """Frames along one straight run: a list of columns, each a list of storeys (bottom-up) with centre_xy, u, base,
    top, opening, inner, confidence. Every upright pair 2..3.3 m apart is scored; overlapping pairs keep the best."""
    q = ln["idx"]; u, nrm, m = ln["u"], ln["nrm"], ln["m"]
    s = (P[q, :2] - m) @ u; t = (P[q, :2] - m) @ nrm; hq = h[q]
    ups = uprights_on_line(s, hq)
    if VERBOSE: print(f"    line at ({m[0]:7.2f},{m[1]:6.2f}) dir ({u[0]:5.2f},{u[1]:5.2f}) n {len(q):5d} span {np.ptp(s):.2f} uprights {np.round(ups, 2)}")
    cands = []
    for i, s1 in enumerate(ups):
        for s2 in ups[i + 1:]:
            if s2 - s1 > OPEN[1]: break
            if s2 - s1 < OPEN[0]: continue
            between = (s > s1 + 0.35) & (s < s2 - 0.35)
            bars = peaks1d(hq[between], H_LO, H_HI, 0.1, 3, 0.8, 0.03)
            thick = {}
            for b in bars:                                                               # a banner is a thick band of splats, a bare tube is thin
                hb = hq[between & (np.abs(hq - b) < 0.5)]
                thick[b] = float(np.percentile(hb, 90) - np.percentile(hb, 10)) if len(hb) > 5 else 0.0
            tops = [b for b in bars if b >= 1.4 and (thick[b] >= 0.25 or not any(thick[c] >= 0.25 for c in bars if c > b))]
            if VERBOSE: print(f"      pair {s1:6.2f}..{s2:6.2f} ({s2 - s1:.2f} m) bars {[(round(b, 2), round(thick[b], 2)) for b in bars]}")
            storeys = []
            for top in tops:
                below = [b for b in bars if top - 3.0 < b < top - 1.2 and b > 0.8]       # a raised frame's bottom bar (the ground tube is not one)
                base = max(below) + 0.08 if below else (0.0 if top < 3.2 else None)     # bottom bar centre + half a tube, or the ground
                if base is None: continue
                edge = hq[between & (hq > top - 0.6) & (hq < top + 0.3)]
                top_edge = float(np.percentile(edge, 5)) if len(edge) > 5 else top - 0.1  # the opening ends at the banner's lower edge
                mid = (hq > base + 0.4) & (hq < top_edge - 0.25)
                ub = mid & ((np.abs(s - s1) < 0.3) | (np.abs(s - s2) < 0.3))
                n_in = int((between & mid).sum()); n_up = int(ub.sum())
                hollow = n_up / max(n_in + n_up, 1)
                tube = float(np.median([np.percentile(s[mid & (np.abs(s - p) < 0.3)], 90) - np.percentile(s[mid & (np.abs(s - p) < 0.3)], 10)
                                        for p in (s1, s2) if (mid & (np.abs(s - p) < 0.3)).sum() >= 3] or [0.2]))
                bar = between & (np.abs(hq - top) < 0.4); span = np.ptp(s[bar]) / (s2 - s1 - 0.7) if bar.sum() > 5 else 0.0
                n = int((((s > s1 - 0.4) & (s < s2 + 0.4)) & (hq > base) & (hq < top + 0.5)).sum())
                conf = min(1.0, hollow / 0.7) * min(1.0, span / 0.8) * min(1.0, n / 150) * (1.0 if thick[top] >= 0.25 else 0.6)
                if not 1.5 <= top_edge - base <= 2.4: conf *= 0.3                           # not a gate-sized opening
                if VERBOSE: print(f"        top {top:.2f} (thick {thick[top]:.2f}, edge {top_edge:.2f}) base {base:.2f} tube {tube:.2f}: hollow {hollow:.2f} bar {span:.2f} n {n} -> conf {conf:.2f}")
                if hollow < 0.4 or span < 0.4 or n < 40 or top_edge - base < 1.2: continue
                c = m + u * (s1 + s2) / 2 + nrm * float(np.median(t[(s > s1) & (s < s2)]))
                storeys.append(dict(centre_xy=c.tolist(), u=u.tolist(), base=float(base), top=float(top_edge), opening=float(s2 - s1),
                                    inner=float(s2 - s1 - tube), tube=tube, hollow=float(hollow), bar_span=float(span), n=n, confidence=round(float(conf), 3)))
            if storeys: cands.append((s1, s2, storeys))
    cols = []; taken = []
    for s1, s2, st in sorted(cands, key=lambda c: -max(x["confidence"] for x in c[2])):   # best first, no overlap beyond 0.5 m
        if all(min(s2, b) - max(s1, a) < 0.5 for a, b in taken): cols.append(st); taken.append((s1, s2))
    return cols


def window_from_cluster(P, h, idx):
    """A flat, square, hollow frame 2.5..4 m across, 2..3.5 m up (prefab 286 lying on its side): None or a dict."""
    Q = P[idx, :2]; hq = h[idx]
    if not (2.0 <= np.median(hq) <= 3.5) or np.percentile(hq, 95) - np.percentile(hq, 5) > 1.0: return None
    m = Q.mean(0); _, _, Vt = np.linalg.svd(Q - m, full_matrices=False); u, v = Vt[0], Vt[1]
    a, b = np.percentile((Q - m) @ u, [1, 99]), np.percentile((Q - m) @ v, [1, 99])
    wa, wb = a[1] - a[0], b[1] - b[0]
    if not (2.4 <= wa <= 4.2 and 2.4 <= wb <= 4.2 and abs(wa - wb) < 0.6): return None
    r = np.max(np.abs(np.c_[(Q - m) @ u, (Q - m) @ v]), 1)
    if (r < 0.6).mean() > 0.1: return None                                              # hollow middle
    c = m + u * (a[0] + a[1]) / 2 + v * (b[0] + b[1]) / 2
    return dict(centre_xy=c.tolist(), u=u.tolist(), base=float(np.median(hq)), top=float(np.median(hq)), opening=float((wa + wb) / 2),
                inner=float((wa + wb) / 2 - 0.35), tube=0.35, n=int(len(idx)), confidence=0.8, window=True)


def to_web(p):
    return [round(float(p[0]), 3), round(float(p[2]), 3), round(float(-p[1]), 3)]


def dirs(u, prior_n):
    """Horizontal width_dir / normal (web frame) from the line direction u (track xy); normal x width_dir = up, and the
    normal is signed like the prior's (travel direction) when one is given."""
    w = np.array([u[0], u[1], 0.0]); n = np.cross(w, [0, 0, 1.0])
    if prior_n is not None and np.dot(to_web(n), prior_n) < 0: w, n = -w, -n
    return [round(float(x), 4) for x in to_web(w)], [round(float(x), 4) for x in to_web(n)]


def web_xy(c):
    return np.array([c[0], -c[2]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ply"); ap.add_argument("track"); ap.add_argument("out")
    ap.add_argument("--opacity", type=float, default=0.2); ap.add_argument("--eps", type=float, default=0.3)
    ap.add_argument("--crops", default="", help="prior gate orders to draw close-ups of (7 m window, top view + elevation)")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); rng = np.random.default_rng(0)
    P, op, rgb = load_ply(a.ply)
    abc, nin = ground_plane(P, op)
    h = P[:, 2] - np.c_[P[:, :2], np.ones(len(P))] @ abc
    print(f"splats {len(P)}, ground z = {abc[0]:.4f} x + {abc[1]:.4f} y + {abc[2]:.3f} ({nin} inliers), tilt {np.degrees(np.hypot(abc[0], abc[1])):.2f} deg")
    grass = (rgb[:, 1] > rgb[:, 0] * 1.08) & (rgb[:, 1] > rgb[:, 2] * 1.08)
    cand = np.flatnonzero((op > a.opacity) & (h > H_LO) & (h < H_HI) & ~grass)
    XY = P[cand, :2]
    cl = clusters(XY, a.eps, 30)
    print(f"candidates {len(cand)} in {len(cl)} clusters (>= 30 splats)")

    cols = []                                                                            # each: a window dict, or a list of storeys
    for ci in cl:
        idx = cand[ci]
        if VERBOSE: print(f"cluster n {len(ci)} at {XY[ci].mean(0).round(2)} extent {np.ptp(XY[ci], 0).round(2)} h p50 {np.median(h[idx]):.2f}")
        w = window_from_cluster(P, h, idx)
        if w: cols.append(w); continue
        for ln in lines(XY, ci, rng):
            ln["idx"] = cand[ln["idx"]]
            cols += frames_on_line(P, h, ln)
    keep = []                                                                            # two lines through one slab: keep the better column
    for d in sorted(cols, key=lambda d: -(d["confidence"] if isinstance(d, dict) else max(x["confidence"] for x in d))):
        d0 = d if isinstance(d, dict) else d[0]
        if all(np.hypot(*(np.array(d0["centre_xy"]) - np.array((e if isinstance(e, dict) else e[0])["centre_xy"]))) > 1.0
               or abs(np.dot(d0["u"], (e if isinstance(e, dict) else e[0])["u"])) < 0.94 for e in keep): keep.append(d)
    cols = keep
    print(f"detected {sum(1 for d in cols if isinstance(d, dict))} windows and {sum(len(d) for d in cols if isinstance(d, list))} frames in {len(cols)} columns")

    # prior: upright gates in the track frame; world (x, y up, z) -> track (x, -z, y)
    T = vd_track.load(a.track)
    tr = lambda v: np.array([v[0], -v[2], v[1]])
    pri = [g for g in T["checkpoints"] if g["prefab"] in vd_track.RF_GATES]
    pc = np.array([tr(g["centre"]) for g in pri]); pw = np.array([tr(g["width_dir"]) for g in pri])
    pcol = [sorted(c, key=lambda i: pc[i, 2]) for c in clusters(pc[:, :2], 0.6, 1)]      # stacked frames share an XY
    C = np.full((len(pcol), len(cols)), 1e6)
    for i, c in enumerate(pcol):
        for j, d in enumerate(cols):
            d0 = d if isinstance(d, dict) else d[0]
            dist = np.hypot(*(np.array(d0["centre_xy"]) - pc[c[0], :2]))
            if dist > MATCH_R: continue
            isw = pri[c[0]]["prefab"] == 286
            if isw != bool(d0.get("window")): dist += 2.0                                 # a window prior should take a window
            wxy = pw[c[0], :2] / max(np.linalg.norm(pw[c[0], :2]), 1e-9)
            ang = 0.0 if isw else np.degrees(np.arccos(min(1.0, abs(float(np.dot(d0["u"], wxy))))))
            C[i, j] = dist + ang / 30
    ri, cj = linear_sum_assignment(C)
    match = {i: j for i, j in zip(ri, cj) if C[i, j] < 1e6}

    gates, openings, inners = [], [], []
    for i, c in enumerate(pcol):
        d = cols[match[i]] if i in match else None
        storeys = ([d] if isinstance(d, dict) else d) if d else []
        for k, gi in enumerate(c):
            g = dict(pri[gi]); g["prior_centre"] = g["centre"]; g["prior_height"] = g["height"]
            s = storeys[k] if k < len(storeys) else None
            if s is None:
                if storeys and not storeys[0].get("window"):                              # column found, this storey not: its XY, the prior's height
                    cxy = np.array(storeys[0]["centre_xy"]); u = np.array(storeys[0]["u"])
                    g["centre"] = to_web([cxy[0], cxy[1], float(np.r_[cxy, 1] @ abc) + pc[gi, 2]])
                    g["width_dir"], g["normal"] = dirs(u, g["normal"]); g["confidence"] = 0.2; g["source"] = "column xy + prior height"
                else:
                    g["centre"] = to_web([pc[gi, 0], pc[gi, 1], float(np.r_[pc[gi, :2], 1] @ abc) + pc[gi, 2]])
                    g["confidence"] = 0.0; g["source"] = "prior (no detection; VD xy, height above the map's ground)"
            else:
                cxy = np.array(s["centre_xy"]); u = np.array(s["u"])
                g["centre"] = to_web([cxy[0], cxy[1], float(np.r_[cxy, 1] @ abc) + s["base"]]); g["confidence"] = s["confidence"]
                g["half_width"] = round(float(s["inner"] / 2), 3); g["opening"] = round(float(s["opening"]), 3); g["inner"] = round(float(s["inner"]), 3)
                if s.get("window"):
                    g["source"] = "window"; g["width_dir"], g["normal"] = pri[gi]["width_dir"], pri[gi]["normal"]    # the tilt is not measured
                else:
                    g["source"] = "map"; g["height"] = round(float(s["top"] - s["base"]), 3)
                    g["width_dir"], g["normal"] = dirs(u, g["normal"]); openings.append(s["opening"]); inners.append(s["inner"])
            off = np.array(g["centre"]) - np.array(g["prior_centre"])
            g["offset_xz_m"] = round(float(np.hypot(off[0], off[2])), 2); g["offset_m"] = [round(float(x), 2) for x in off]
            gates.append(g)
    extra = []
    for j, d in enumerate(cols):
        if j in match.values(): continue
        for s in ([d] if isinstance(d, dict) else d):
            cxy = np.array(s["centre_xy"]); wd, nm = dirs(np.array(s["u"]), None)
            extra.append(dict(centre=to_web([cxy[0], cxy[1], float(np.r_[cxy, 1] @ abc) + s["base"]]), width_dir=wd, normal=nm,
                              half_width=round(s["inner"] / 2, 3), height=round(s["top"] - s["base"], 3), opening=round(s["opening"], 3),
                              confidence=s["confidence"], kind="window" if s.get("window") else "frame", n_splats=s["n"]))
    # every checkpoint: detected uprights replace the prior entries, the others (flags, invisible volumes) stay as VD placed them
    by_order = {g["order"]: g for g in gates}
    cps = [by_order.get(g["order"], dict(g, confidence=0.0, source="prior (not a gate prefab)")) for g in T["checkpoints"]]
    fin = next(g for g in cps if g["start"] and g["finish"])
    st = lambda v: dict(n=len(v), mean=round(float(np.mean(v)), 3), median=round(float(np.median(v)), 3), std=round(float(np.std(v)), 3),
                        min=round(float(np.min(v)), 3), max=round(float(np.max(v)), 3)) if len(v) else None
    out = dict(frame="world: metres, x = VelociDrone x, y = up, z = -VelociDrone z", ply=a.ply, track=a.track, track_name=T["name"],
               ground_plane_track=dict(z_eq="a x + b y + c", abc=[round(float(x), 5) for x in abc], inliers=nin),
               params=dict(opacity=a.opacity, eps=a.eps),
               width_check=dict(opening_upright_centres=st(openings), inner=st(inners), expected_inner=[2.13, 2.17]),
               gate={k: fin[k] for k in ("centre", "normal", "width_dir", "half_width", "height")}, checkpoints=cps, extra=extra)
    json.dump(out, open(f"{a.out}/gates.json", "w"), indent=1)
    n_det = sum(1 for g in gates if g["source"] in ("map", "window"))
    print(f"prior upright gates {len(pri)} in {len(pcol)} columns; detected {n_det}, column-only {sum(1 for g in gates if g['source'].startswith('column'))}, "
          f"unmatched {sum(1 for g in gates if g['confidence'] == 0)}, extra detections {len(extra)}")
    print("opening (upright centres):", st(openings)); print("inner (minus tube):", st(inners))
    for g in gates:
        print(f"  gate {g['order']:2d} {g['name']:<19} conf {g['confidence']:.2f} {g['source']:<24.24} offset xz {g['offset_xz_m']:.2f} m (dx dy dz {g['offset_m']})"
              + (f"  opening {g['opening']} inner {g['inner']} height {g['height']}" if "opening" in g else ""))
    for e in extra: print(f"  extra {e['kind']} at {e['centre']} opening {e['opening']} height {e['height']} conf {e['confidence']} ({e['n_splats']} splats)")
    plot_top(P, h, op, a, pc, pw, gates, extra)
    for o_ in [int(x) for x in a.crops.split(",") if x]:
        plot_crop(P, h, op, rgb, a, o_, gates, extra, abc, pri)


def plot_top(P, h, op, a, pc, pw, gates, extra):
    k = (op > a.opacity) & (h > H_LO) & (h < H_HI)
    lo, hi = np.percentile(pc[:, :2], 0, 0) - 8, np.percentile(pc[:, :2], 100, 0) + 8
    fig, ax = plt.subplots(figsize=(12, 12 * (hi[1] - lo[1]) / (hi[0] - lo[0])))
    ax.hist2d(P[k, 0], P[k, 1], bins=[np.arange(lo[0], hi[0], 0.25), np.arange(lo[1], hi[1], 0.25)], cmap="Greys", vmax=30)
    for c, w in zip(pc, pw):
        ax.plot([c[0] - w[0] * 1.1, c[0] + w[0] * 1.1], [c[1] - w[1] * 1.1, c[1] + w[1] * 1.1], c="deepskyblue", lw=1.2, alpha=0.8)
        ax.scatter([c[0]], [c[1]], s=40, marker="s", facecolors="none", edgecolors="deepskyblue")
    for g in gates:
        c = web_xy(g["centre"]); w = web_xy(g["width_dir"]); n = web_xy(g["normal"]); hw = g["half_width"] or 1.1
        col = "red" if g["source"] in ("map", "window") else "orange"
        if col == "red": ax.plot([c[0] - w[0] * hw, c[0] + w[0] * hw], [c[1] - w[1] * hw, c[1] + w[1] * hw], c=col, lw=2)
        ax.scatter([c[0]], [c[1]], s=18, c=col); ax.arrow(c[0], c[1], n[0] * 1.5, n[1] * 1.5, color=col, head_width=0.4, lw=0.8)
        ax.annotate(f"{g['order']}", (c[0], c[1]), color=col, fontsize=8, xytext=(4, 4), textcoords="offset points")
    for e in extra:
        c = web_xy(e["centre"]); w = web_xy(e["width_dir"]); hw = e["half_width"]
        ax.plot([c[0] - w[0] * hw, c[0] + w[0] * hw], [c[1] - w[1] * hw, c[1] + w[1] * hw], c="magenta", lw=2)
        ax.annotate("extra", (c[0], c[1]), color="magenta", fontsize=8, xytext=(4, -10), textcoords="offset points")
    ax.set_aspect("equal"); ax.grid(True, lw=0.3)
    ax.set_title(f"{os.path.basename(a.ply)}: splats {H_LO}..{H_HI} m up (grey), VD prior (blue, hollow), detected (red, arrow = travel normal), "
                 f"prior kept (orange), extra (magenta)", fontsize=9)
    fig.savefig(f"{a.out}/gates-top.png", dpi=100, bbox_inches="tight"); plt.close(fig)


def plot_crop(P, h, op, rgb, a, order, gates, extra, abc, pri):
    g = next((g for g in gates if g["order"] == order), None)
    if g is None: return
    c = web_xy(g["centre"]); w = web_xy(g["width_dir"]); n = web_xy(g["normal"]); hw = g["half_width"] or 1.1; r = 3.5
    k = (op > a.opacity) & (h > 0.05) & (h < H_HI) & (np.abs(P[:, 0] - c[0]) < r) & (np.abs(P[:, 1] - c[1]) < r)
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(14, 7))
    o = np.argsort(h[k]); ax.scatter(P[k, 0][o], P[k, 1][o], c=rgb[k][o], s=4, linewidths=0)
    p = pri[[q["order"] for q in pri].index(order)]; pcxy = web_xy(p["centre"]); pw = web_xy(p["width_dir"])
    ax.plot([pcxy[0] - pw[0] * 1.1, pcxy[0] + pw[0] * 1.1], [pcxy[1] - pw[1] * 1.1, pcxy[1] + pw[1] * 1.1], c="deepskyblue", lw=2, label="VD prior")
    if g["source"] in ("map", "window"):
        ax.plot([c[0] - w[0] * hw, c[0] + w[0] * hw], [c[1] - w[1] * hw, c[1] + w[1] * hw], c="red", lw=2, label="detected")
        ax.arrow(c[0], c[1], n[0], n[1], color="red", head_width=0.2)
    for e in extra:
        ec = web_xy(e["centre"]); ew = web_xy(e["width_dir"])
        if np.hypot(*(ec - c)) < 5: ax.plot([ec[0] - ew[0] * e["half_width"], ec[0] + ew[0] * e["half_width"]], [ec[1] - ew[1] * e["half_width"], ec[1] + ew[1] * e["half_width"]], c="magenta", lw=2, label="extra")
    ax.set_facecolor("k"); ax.set_aspect("equal"); ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.legend(fontsize=8, loc="upper right")
    ax.set_title(f"gate {order} {g['name']} top view (track xy), conf {g['confidence']:.2f}, {g['source']}", fontsize=9)
    rel = P[:, :2] - c; s = rel @ w; t = rel @ n                                             # elevation: splats near the gate plane
    kk = (op > a.opacity) & (h > -0.2) & (h < H_HI) & (np.abs(s) < 3.0) & (np.abs(t) < 0.6)
    bx.scatter(s[kk], h[kk], c=rgb[kk], s=4, linewidths=0)
    base = g["centre"][1] - float(np.r_[c, 1] @ abc)
    if g["source"] in ("map", "window"):
        bx.axvline(-hw, c="red", lw=1); bx.axvline(hw, c="red", lw=1); bx.axhline(base, c="red", lw=1); bx.axhline(base + g["height"], c="red", lw=1)
    bx.set_facecolor("k"); bx.set_aspect("equal"); bx.set_xlim(-3, 3); bx.set_ylim(-0.2, 5); bx.grid(True, lw=0.3, color="0.4")
    bx.set_title(f"elevation along the frame (splats within 0.6 m of its plane); red = opening {2 * hw:.2f} m x {g['height']:.2f} m from {base:.2f} m", fontsize=9)
    bx.set_xlabel("along width_dir (m)"); bx.set_ylabel("height above ground (m)")
    fig.savefig(f"{a.out}/gates-crop-{order}.png", dpi=100, bbox_inches="tight"); plt.close(fig)


if __name__ == "__main__":
    main()
