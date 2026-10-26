"""MGP's register.py (rt4090 ~/mgp) for JDL R7: add a heat's image folders to an existing SfM model without moving it.
Changed from MGP's: the mapper sees only the base model's images and the new ones (image_names), and the new images are
matched against about 1,000 images of the base model directly, not by vocab-tree retrieval. With every heat of the night in
the database, the MGP version made the mapper try thousands of unrelated images (45 min a heat) and the retrieval picked
other heats' frames (0 of 511 registered); this one takes 2-7 min a heat.
usage: register3.py <work_dir> <vocab_tree.bin (unused)> <in_model> <out_model> <folder>...   (images and masks under <work_dir>)"""
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
# every new image against every other image of the base model, not vocab-tree retrieval: with every heat of the night in the
# database, the 50 nearest were other heats' frames and no new image met the base (JDL R7 10/25: 0 of 511 registered)
allb = sorted(im.name for im in p.Reconstruction(model_in).images.values()); base = allb[::max(1, len(allb) // 1000)]   # about 1,000 of a growing model
pl = f"{w}/pairs_{os.getpid()}.txt"; open(pl, "w").write("".join(f"{n} {b}\n" for n in names for b in base))
p.match_image_pairs(db, pairing_options=p.ImportedPairingOptions(match_list_path=pl))
log(f"pairs ({len(names)} x {len(base)})")
o = p.IncrementalPipelineOptions()
o.fix_existing_frames = True
o.multiple_models = False
# only the base model's images and the new ones: the database holds every heat cut tonight, and the mapper otherwise tries
# to register all of them (JDL R7 10/25: 45 min for 516 new images, with thousands of others waiting in the database)
o.image_names = sorted({im.name for im in p.Reconstruction(model_in).images.values()} | set(names))
o.mapper.abs_pose_min_num_inliers = int(os.environ.get("MIN_INL", 30))
os.makedirs(model_out, exist_ok=True)
recs = p.incremental_mapping(db, img, model_out, o, input_path=model_in)
log("map")
r = recs[0] if 0 in recs else max(recs.values(), key=lambda r: r.num_reg_images())
got = {im.name for im in r.images.values()}
log(f"registered {sum(n in got for n in names)}/{len(names)} new; model {r.num_reg_images()} images")
