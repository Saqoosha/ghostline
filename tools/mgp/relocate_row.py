"""Localize again, against the unfolded map (mapfix_unfold.py), the frames of a round that the old map placed near
the final row, where it held the row twice and most frames took the copy 3.2 m inside; merge them into the round's
localization. The rest of the course was one copy, so the other frames keep their poses.

  stage <out_root> <top_frame.json> <fit.json> <round loc dir>...      (e.g. ~/mgp/rounds/q45_loc)
      per cell json: the ok frames whose centre is in CBOX (track frame) are the targets
      (<out_root>/<qNN>/targets.json); they and the frames within CONTEXT_MS of them (so the tracker keeps its
      prediction going into the row) are linked under <out_root>/<qNN>/images/<cell>/ (masks under .../masks/<cell>/),
      and <out_root>/<qNN>/args.txt holds "--lens <cell>=<lens>" per cell, the lens the round was localized with
  merge <out_root> <round loc dir> <out loc dir> <top_frame.json> <fit.json>
      the cell jsons with each target replaced by its new result; a target localized again with fewer than MIN_INL
      inliers, not at all, or more than MAX_MOVE m from where it was (it belongs where it was or 3.2 m from there;
      farther is the same print elsewhere on the course: 43 of 355 on Round 45) becomes ok: false
Run localize.py per round in between:  localize.py <map dir> <out_root>/<qNN>/loc <out_root>/<qNN>/images/* $(cat args.txt)
(relocate_all.sh does the three for every round.)
"""
import json, os, sys
import numpy as np

CBOX = [float(v) for v in os.environ.get("CBOX", "-131,-104,-8,22,-2,15").split(",")]
CONTEXT_MS = int(os.environ.get("CONTEXT_MS", 1000))


def metric_of(frame_json, fit_json):
    F, T = json.load(open(frame_json)), json.load(open(fit_json))
    E, a0 = np.array([F["ex"], F["ey"], F["up"]]), np.array(F["origin"])
    R2 = np.array(T["R"]) @ np.diag([1, T["mirror"]])

    def metric(X):
        G = (np.atleast_2d(X) - a0) @ E.T * T["scale"]
        return np.c_[G[:, :2] @ R2.T + T["t"], G[:, 2]]
    return metric


def stage(out_root, frame_json, fit_json, *loc_dirs):
    metric = metric_of(frame_json, fit_json)
    for ld in loc_dirs:
        ld = ld.rstrip("/"); q = os.path.basename(ld)[:-4]; base = ld[:-4]      # rounds/q45_loc -> q45, rounds/q45
        args, targets, n = [], {}, 0
        for fn in sorted(os.listdir(ld)):
            if not fn.endswith(".json"):
                continue
            cell = fn[:-5]; J = json.load(open(f"{ld}/{fn}"))
            F = [f for f in J["frames"] if f["ok"]]
            if not F:
                continue
            C = metric(np.array([f["center"] for f in F]))
            k = (C[:, 0] > CBOX[0]) & (C[:, 0] < CBOX[1]) & (C[:, 1] > CBOX[2]) & (C[:, 1] < CBOX[3]) & (C[:, 2] > CBOX[4]) & (C[:, 2] < CBOX[5])
            tgt = sorted(f["t_ms"] for f, keep in zip(F, k) if keep)
            if not tgt:
                continue
            on_disk = sorted(int(f[:-4]) for f in os.listdir(f"{base}/images/{cell}") if f.endswith(".jpg"))
            T = np.array(tgt)
            take = [t for t in on_disk if np.abs(T - t).min() <= CONTEXT_MS]
            for kind, ext in (("images", ""), ("masks", ".png")):
                os.makedirs(f"{out_root}/{q}/{kind}/{cell}", exist_ok=True)
                for t in take:
                    name = "%07d.jpg%s" % (t, ext)
                    dst = f"{out_root}/{q}/{kind}/{cell}/{name}"
                    if not os.path.lexists(dst):
                        os.symlink(os.path.abspath(f"{base}/{kind}/{cell}/{name}"), dst)
            lens = J.get("camera", {}).get("lens", "auto")
            args.append(f"--lens {cell}={lens if lens in ('stock', 'upgrade', 'median') else 'auto'}")
            targets[cell] = tgt; n += len(tgt)
        if args:
            open(f"{out_root}/{q}/args.txt", "w").write(" ".join(args) + "\n")
            json.dump(targets, open(f"{out_root}/{q}/targets.json", "w"))
        print(f"{q}: {n} frames near the row to place again")


def merge(out_root, loc_dir, out_dir, frame_json, fit_json):
    MIN_INL, MAX_MOVE = int(os.environ.get("MIN_INL", 20)), float(os.environ.get("MAX_MOVE", 5))
    metric = metric_of(frame_json, fit_json)
    q = os.path.basename(loc_dir.rstrip("/"))[:-4]
    targets = json.load(open(f"{out_root}/{q}/targets.json"))
    os.makedirs(out_dir, exist_ok=True)
    for fn in sorted(os.listdir(loc_dir)):
        if not fn.endswith(".json"):
            continue
        J = json.load(open(f"{loc_dir}/{fn}"))
        tgt = set(targets.get(fn[:-5], []))
        new_p = f"{out_root}/{q}/loc/{fn}"
        N = {f["t_ms"]: f for f in json.load(open(new_p))["frames"]} if os.path.exists(new_p) else {}
        moved = far = failed = 0
        for i, f in enumerate(J["frames"]):
            if f["t_ms"] not in tgt:
                continue
            g = N.get(f["t_ms"])
            if g and g["ok"] and g["inliers"] >= MIN_INL:
                if np.linalg.norm(metric(np.array(g["center"]))[0] - metric(np.array(f["center"]))[0]) <= MAX_MOVE:
                    J["frames"][i] = g; moved += 1; continue
                far += 1
            else:
                failed += 1
            J["frames"][i] = {"t_ms": f["t_ms"], "ok": False, "inliers": g["inliers"] if g else 0}
        J["relocated"] = {"targets": len(tgt), "replaced": moved, "too_far": far, "failed": failed}
        json.dump(J, open(f"{out_dir}/{fn}", "w"))
        print(f"{q} {fn[:-5]}: {moved} replaced, {far} too far, {failed} failed of {len(tgt)} near the row")


if __name__ == "__main__":
    {"stage": stage, "merge": merge}[sys.argv[1]](*sys.argv[2:])
