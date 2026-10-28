"""SfM over the per-pilot frames from extract.py: one OPENCV_FISHEYE camera per pilot folder,
SIFT with the OSD/prop masks, exhaustive matching, then the incremental or global mapper.
usage: sfm.py <work_dir>   (env MAPPER=incremental|global, default incremental; STEP=all|map)"""
import os, sys, time
import pycolmap as p

w = sys.argv[1]
db, img, msk, out = f"{w}/database.db", f"{w}/images", f"{w}/masks", f"{w}/sparse"
MAPPER, STEP = os.environ.get("MAPPER", "incremental"), os.environ.get("STEP", "all")
t = time.time()
if STEP == "all":
    if os.path.exists(db):
        os.remove(db)
    ro = p.ImageReaderOptions(camera_model="OPENCV_FISHEYE", mask_path=msk,
                              camera_params="238,238,240,180,0,0,0,0")   # HDZero 4:3 feed at 480x360
    eo = p.FeatureExtractionOptions()
    eo.sift.max_num_features = 4096
    p.extract_features(db, img, camera_mode=p.CameraMode.PER_FOLDER, reader_options=ro, extraction_options=eo)
    print("extract", round(time.time() - t), "s", flush=True)
    p.match_exhaustive(db)
    print("match", round(time.time() - t), "s", flush=True)
os.makedirs(out, exist_ok=True)
if MAPPER == "global":
    recs = p.global_mapping(db, img, out)
else:
    o = p.IncrementalPipelineOptions()
    o.multiple_models = True
    recs = p.incremental_mapping(db, img, out, o)
print("map", round(time.time() - t), "s", flush=True)
for k, r in recs.items():
    print(k, r.summary())
