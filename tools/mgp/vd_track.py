"""Load a decrypted VelociDrone track (the 3-line text from Velbd: scene id, name, JSON) and export its gates in the race
page's WORLD FRAME: metres, y up, x = VelociDrone x, z = -VelociDrone z (Unity is left-handed, so z is flipped once).
usage: vd_track.py <track.txt> [out.json]      -> start/finish gate + ordered checkpoints as JSON

Placement (what Velbd's viewer does, src/client-3d/track/scene-builder/placement-loader.ts):
- pos is cm; rot is (w, x, y, z) x1000 on the wire; scale x100.
- the world pose is  p = (x, y, -z),  q = (-qx, -qy, qz, qw)  (Three.js, right-handed).
Gate opening: the prefab's trigger box (the volume VelociDrone judges a pass against) read from Velbd's .vmsh mesh
(v1 header: part bbox + 4x3 matrix), times the placement scale. DefaultMGPRF (prefab 285) = 2.17 m wide x 1.90 m tall
(~7 x 6 ft), base on the floor. Override Velbd's location with the VELBD env var.
Direction: a gate has no inherent front. normal is signed to the direction of travel, (next gate - previous gate) in
checkpoint order, wrapping 33 -> 1. width_dir is the gate's local x in the world frame, normal x width_dir = up.
"""
import csv, json, os, struct, sys
import numpy as np

VELBD = os.environ.get("VELBD", "/Users/hiko/repos/Personal/Velbd")
# fallback when the Velbd mesh is not on disk: prefab 285's trigger box (half width, height, centre height above base)
FALLBACK = {285: (1.087, 1.90)}
RF_GATES = {285, 286, 283}   # DefaultMGPRF, ...Window, ...TallRF2: upright gates whose trigger box is the opening


def _prefab_names():
    try:
        with open(f"{VELBD}/data/trackprefabs.csv") as f:
            return {int(r["id"]): (r["name"], r["type"], r["bundle"]) for r in csv.DictReader(f)}
    except OSError:
        return {}


def _trigger_box(bundle, name):
    """(half width, height, base) of the prefab's _trigger_box in its own frame, or None (vmsh v1 only)."""
    try:
        b = open(f"{VELBD}/public/meshes/{bundle}/{name}.vmsh", "rb").read()
    except OSError:
        return None
    if b[4] != 1:
        return None
    n = struct.unpack_from("<H", b, 5)[0]; o = 8
    for _ in range(n):
        st, ib, nl, vc, ic, ep, ei = struct.unpack_from("<BBHIIII", b, o); o += 20
        M = np.array(struct.unpack_from("<16f", b, o)).reshape(4, 4); o += 64
        q = struct.unpack_from("<6f", b, o); o += 24
        nm = b[o:o + nl].decode(); o += nl + ep + ei
        if st == 3:                                                            # source tag 3 = trigger
            lo = np.array(q[:3]); hi = lo + np.array(q[3:])
            c = np.array([[x, y, z, 1] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]) @ M.T
            return (float((c[:, 0].max() - c[:, 0].min()) / 2), float(c[:, 1].max()), float(c[:, 1].min()))
    return None


def _rotate(q, v):                                                              # q = (x, y, z, w)
    x, y, z, w = q; u = np.array([x, y, z])
    return v + 2 * np.cross(u, np.cross(u, v) + w * v)


def load(path):
    L = open(path).read().split("\n")
    d = json.loads(L[2]); names = _prefab_names(); cache = {}
    gates = []
    for g in d["gates"]:
        t = g["trans"]; w, x, y, z = (np.array(t["rot"], float) / max(np.linalg.norm(t["rot"]), 1e-9))
        q = (-x, -y, z, w)                                                      # VelociDrone (left-handed) -> world
        nm, kind, bundle = names.get(g["prefab"], (f"prefab{g['prefab']}", "?", "gates"))
        if g["prefab"] not in cache:
            tb = _trigger_box(bundle, nm)
            cache[g["prefab"]] = (tb[0], tb[1]) if tb else FALLBACK.get(g["prefab"])
        sz = cache[g["prefab"]] if g["prefab"] in RF_GATES else None            # other prefabs' boxes are rotated / flag-shaped: not an opening
        sc = np.array(t["scale"], float) / 100
        pos = np.array([t["pos"][0], t["pos"][1], -t["pos"][2]]) / 100
        gates.append(dict(
            order=g["gate"], prefab=g["prefab"], name=nm, kind=kind, start=g["start"], finish=g["finish"],
            centre=pos.round(3).tolist(), width_dir=_rotate(q, np.array([1.0, 0, 0])).round(4).tolist(),
            normal=_rotate(q, np.array([0, 0, 1.0])).round(4).tolist(),
            half_width=round(float(sz[0] * sc[0]), 3) if sz else None, height=round(float(sz[1] * sc[1]), 3) if sz else None))
    cp = sorted((g for g in gates if g["order"] > 0), key=lambda g: g["order"])
    for i, g in enumerate(cp):                                                  # sign the normal to the travel direction
        d_ = np.array(cp[(i + 1) % len(cp)]["centre"]) - np.array(cp[i - 1]["centre"])
        if np.dot(g["normal"], d_) < 0:
            g["normal"] = (-np.array(g["normal"])).round(4).tolist()
            g["width_dir"] = (-np.array(g["width_dir"])).round(4).tolist()
    return dict(scene=int(L[0]), name=L[1], gates=gates, checkpoints=cp)


def finish_gate(track):
    """The start/finish gate as make_race.py's gate dict (centre on the floor, y up)."""
    g = next(g for g in track["checkpoints"] if g["start"] and g["finish"])
    return {k: g[k] for k in ("centre", "normal", "width_dir", "half_width", "height")}


if __name__ == "__main__":
    T = load(sys.argv[1]); out = dict(scene=T["scene"], name=T["name"], gate=finish_gate(T), checkpoints=T["checkpoints"])
    if len(sys.argv) > 2:
        json.dump(out, open(sys.argv[2], "w"), indent=1)
    print(T["name"], "start/finish", finish_gate(T), f"{len(T['checkpoints'])} checkpoints")
