"""Add new image folders (e.g. one pilot's race at high fps) to an existing SfM model without
moving it: features for the new images only, pairs = sequential neighbours + vocab-tree
retrieval of the new images against everything, then the incremental mapper continues from the
existing model with its frames fixed.
usage: register.py <work_dir> <vocab_tree.bin> <in_model> <out_model> <folder>...
The folders must already be under <work_dir>/images (and masks under <work_dir>/masks)."""
import os, sys, time
import pycolmap as p

w, vocab, model_in, model_out = sys.argv[1:5]
folders = sys.argv[5:]
db, img, msk = f"{w}/database.db", f"{w}/images", f"{w}/masks"
t = time.time()
log = lambda s: print(f"[{time.time() - t:7.0f}s] {s}", flush=True)
names = sorted(f"{d}/{f}" for d in folders for f in os.listdir(f"{img}/{d}") if f.endswith(".jpg"))
log(f"{len(names)} new images")
ro = p.ImageReaderOptions(camera_model="OPENCV_FISHEYE", mask_path=msk, camera_params=os.environ.get("CAM", "195,197,240,180,0.155,-0.09,0.026,-0.003"))   # race-1 map median (upgrade lens)
eo = p.FeatureExtractionOptions()
eo.sift.max_num_features = 4096
p.extract_features(db, img, image_names=names, camera_mode=p.CameraMode.PER_FOLDER, reader_options=ro, extraction_options=eo)
log("extract")
p.match_sequential(db, pairing_options=p.SequentialPairingOptions(overlap=15, quadratic_overlap=True))
log("sequential")
ql = f"{w}/query_{os.getpid()}.txt"
open(ql, "w").write("\n".join(names) + "\n")
p.match_vocabtree(db, pairing_options=p.VocabTreePairingOptions(num_images=50, vocab_tree_path=vocab, match_list_path=ql))
log("vocabtree")
o = p.IncrementalPipelineOptions()
o.fix_existing_frames = True
o.multiple_models = False
o.mapper.abs_pose_min_num_inliers = int(os.environ.get("MIN_INL", 30))
os.makedirs(model_out, exist_ok=True)
recs = p.incremental_mapping(db, img, model_out, o, input_path=model_in)
log("map")
r = recs[0] if 0 in recs else max(recs.values(), key=lambda r: r.num_reg_images())
got = {im.name for im in r.images.values()}
log(f"registered {sum(n in got for n in names)}/{len(names)} new; model {r.num_reg_images()} images")
