"""A training folder for spirula from a map model: images/ and masks/ as per-folder symlinks to the extracted frames (a
model folder name can differ from the one on disk: RENAME "r01_:r1034_"), and sparse/0 = the model without the images
whose files are missing.
usage: gs_dataset.py <model> <image root> <mask root> <out dir>"""
import os, sys
import pycolmap as p

model, iroot, mroot, out = sys.argv[1:5]
ren = dict(x.split(":") for x in os.environ.get("RENAME", "r01_:r1034_").split() if x)
disk = lambda folder: next((b + folder[len(a):] for a, b in ren.items() if folder.startswith(a)), folder)
R = p.Reconstruction(model)
os.makedirs(f"{out}/images", exist_ok=True); os.makedirs(f"{out}/masks", exist_ok=True)
folders = sorted({im.name.split("/")[0] for im in R.images.values()})
for f in folders:
    for sub, root in (("images", iroot), ("masks", mroot)):
        link = f"{out}/{sub}/{f}"
        if not os.path.islink(link):
            os.symlink(os.path.abspath(f"{root}/{disk(f)}"), link)   # a relative target would resolve from out/<sub>/
missing = [im for im in R.images.values() if not os.path.exists(f"{out}/images/{im.name}")]
for im in missing:
    R.deregister_frame(im.frame_id)
os.makedirs(f"{out}/sparse/0", exist_ok=True)
R.write(f"{out}/sparse/0")
print(f"{len(folders)} folders, {R.num_reg_images()} images ({len(missing)} missing dropped), {R.num_points3D()} points -> {out}")
