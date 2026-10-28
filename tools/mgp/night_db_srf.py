"""The night map's database with one-term fisheye cameras: a copy of night_sfm_hloc.py's database (OPENCV_FISHEYE, four
free distortion terms that the far lights at night cannot pin down; the bundle adjustment stalled 55 minutes at a time)
with every camera set to SIMPLE_RADIAL_FISHEYE (focal F, 320, distortion 0, centre at the middle of the frame), then the
two-view geometries verified again for the new cameras. Map it with REUSE_DB=1 DB=<out name> night_sfm_hloc.py.
usage (rt4090, in night/, ~/mastenv): night_db_srf.py <sfm dir> <source db> <out db>    e.g. sk/sfm database_v1.db database_srf.db"""
import os, shutil, sys, time
from pathlib import Path
import pycolmap
from hloc.triangulation import estimation_and_geometric_verification

sfm, src, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
F = float(os.environ.get("F", 320))
t = time.time(); shutil.copyfile(sfm / src, sfm / out)
with pycolmap.Database.open(str(sfm / out)) as db:
    n = 0
    for cam in db.read_all_cameras():
        cam.model = pycolmap.CameraModelId.SIMPLE_RADIAL_FISHEYE; cam.params = [F, cam.width / 2, cam.height / 2, 0.0]
        cam.has_prior_focal_length = True; db.update_camera(cam); n += 1
    db.clear_two_view_geometries()
print(n, f"cameras -> SIMPLE_RADIAL_FISHEYE f {F:g}, k 0", flush=True)
estimation_and_geometric_verification(sfm / out, sfm / "pairs.txt", False)
print("verified in", round(time.time() - t), "s")
