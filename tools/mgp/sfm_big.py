"""SfM for many races at once: exhaustive matching does not scale, so pairs come from
(a) sequential neighbours inside each pilot's folder and (b) vocabulary-tree retrieval across
everything (other laps, other pilots, other races). Then the global mapper.
usage: sfm_big.py <work_dir> <vocab_tree.bin>   (env STEP=all|match|map, MAPPER=global|incremental, NN=30)"""
import os, sys, time
import pycolmap as p

w, vocab = sys.argv[1], sys.argv[2]
db, img, msk, out = f"{w}/database.db", f"{w}/images", f"{w}/masks", f"{w}/sparse"
STEP, MAPPER, NN = os.environ.get("STEP", "all"), os.environ.get("MAPPER", "global"), int(os.environ.get("NN", 30))
t = time.time()
log = lambda s: print(f"[{time.time() - t:7.0f}s] {s}", flush=True)
if STEP == "all":
    if os.path.exists(db):
        os.remove(db)
    ro = p.ImageReaderOptions(camera_model="OPENCV_FISHEYE", mask_path=msk, camera_params="238,238,240,180,0,0,0,0")
    eo = p.FeatureExtractionOptions()
    eo.sift.max_num_features = 4096
    p.extract_features(db, img, camera_mode=p.CameraMode.PER_FOLDER, reader_options=ro, extraction_options=eo)
    log("extract")
if STEP in ("all", "match"):
    so = p.SequentialPairingOptions(overlap=10, quadratic_overlap=True)
    p.match_sequential(db, pairing_options=so)
    log("sequential")
    vo = p.VocabTreePairingOptions(num_images=NN, vocab_tree_path=vocab)
    p.match_vocabtree(db, pairing_options=vo)
    log("vocabtree")
os.makedirs(out, exist_ok=True)
if MAPPER == "global":
    recs = p.global_mapping(db, img, out)
else:
    o = p.IncrementalPipelineOptions()
    o.multiple_models = True
    recs = p.incremental_mapping(db, img, out, o)
log("map")
for k, r in recs.items():
    print(k, r.summary())
