"""SfM over the per-pilot frames from extract.py: one OPENCV_FISHEYE camera per pilot folder,
SIFT with the OSD/prop masks, exhaustive matching, then the incremental or global mapper.
usage: sfm.py <work_dir>   (env MAPPER=incremental|global, default incremental; STEP=all|map;
       MATCH=seqvoc VOCAB=<vocab tree .bin> [OVERLAP=20 VOC_N=50]: sequential + vocabulary-tree pairs instead of exhaustive)"""
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
    if os.environ.get("MATCH") == "seqvoc":
        # many frames (6 fps): each feed's neighbours in time, plus the vocabulary tree's look-alikes across feeds
        so = p.SequentialPairingOptions(); so.overlap = int(os.environ.get("OVERLAP", 20))
        p.match_sequential(db, pairing_options=so)
        vo = p.VocabTreePairingOptions(); vo.vocab_tree_path = os.environ["VOCAB"]; vo.num_images = int(os.environ.get("VOC_N", 50))
        p.match_vocabtree(db, pairing_options=vo)
    else:
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
