"""The audience pages' scene folder for a day (tools/publish-event.sh <event> <folder>): index.json (the scene's name, the orbit
round the course, title), track.json (the course line in the scene's frame) and scene/ for the .sog, added later.
  python make_scene_folder.py <course.json: course_splice.py's, "track" in the scene frame> <folder> <scene name> <title>"""
import json, os, sys
import numpy as np
course, folder, scene, title = sys.argv[1:5]
t = np.array(json.load(open(course))["track"])
lo, hi = t.min(0), t.max(0); mid = (lo + hi) / 2
os.makedirs(f"{folder}/scene", exist_ok=True)
json.dump(dict(track=t.round(3).tolist()), open(f"{folder}/track.json", "w"))
ix = dict(scene=scene, orbit=dict(target=[round(mid[0], 1), round(float(np.median(t[:, 1])), 1), round(mid[2], 1)],
                                  dist=round(float(max(hi[0] - lo[0], hi[2] - lo[2])) * 0.9, 0), yaw=0, pitch=-45),
          track="track.json", title=title)
json.dump(ix, open(f"{folder}/index.json", "w"), ensure_ascii=False)
print(json.dumps(ix, ensure_ascii=False)); print(f"track {len(t)} points, x {lo[0]:.0f}..{hi[0]:.0f} z {lo[2]:.0f}..{hi[2]:.0f} -> {folder}")
