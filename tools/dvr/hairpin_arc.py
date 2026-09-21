"""Slerp between two anchors takes the short arc in SO(3); through a hairpin the drone may have
gone the long way round. For the listed gaps replace the interpolated quaternions with the long-arc
slerp (quaternion dot forced negative). Which arc is right is decided photometrically
(eval_bands.py BLUR=3 on both): 2026-09-21, of 7 gaps with >= 100 deg between anchors only
#1662-#1673 preferred the long arc (loss 1.196 -> 1.070, 9/10 frames); the other six keep the short arc.
usage: hairpin_arc.py init.json out.json a-b[,a-b...]"""
import json, sys, math, numpy as np
P = json.load(open(sys.argv[1])); poses = P["poses"]
for g in sys.argv[3].split(","):
    a, b = map(int, g.split("-")); qa = np.array(poses[a]["quat"]); qb = np.array(poses[b]["quat"])
    if np.dot(qa, qb) > 0: qb = -qb
    th = math.acos(max(-1, min(1, float(np.dot(qa, qb)))))
    for i in range(a + 1, b):
        t = (i - a) / (b - a); q = (math.sin((1 - t) * th) * qa + math.sin(t * th) * qb) / math.sin(th)
        poses[i]["quat"] = np.round(q / np.linalg.norm(q), 6).tolist(); poses[i]["arc"] = "long"
    print(f"#{a}-#{b}: long arc, {b-a-1} frames, {math.degrees(2*th):.0f} deg")
json.dump(P, open(sys.argv[2], "w"))
