"""Is DVR frame i the picture pose i was made from? For each given frame, render pose i once and score
it against DVR frames i-2..i+2 (band loss, whole-frame normalisation); report the best offset. A
systematic +1/-1 means the COLMAP jpg index and the pinhole mp4 index differ by a frame.
YAW=i: instead, sweep yaw -6..+6 deg on pose i against DVR frame i and print the loss per band."""
import json, sys, math, os, numpy as np, torch, cv2
sys.argv += []  # reuse eval_bands' scene/camera setup by exec
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_bands.py")).read()
head = src.split("PI = json.load")[0]                     # everything up to the frame loop: scene, camera, loss helpers
sys.argv = sys.argv[:4] + [sys.argv[3]] + sys.argv[4:]; exec(head)   # eval_bands wants a ref_json slot
PI = json.load(open(init_json))["poses"]
cap = cv2.VideoCapture(video)
def dvr(i):
    cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, fr = cap.read(); fr = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
    sky = cv2.resize(sky_mask(fr).astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
    f = torch.tensor(cv2.resize(fr, (w, h), interpolation=cv2.INTER_AREA), dtype=torch.float32, device=dev) / 255
    base = torch.tensor(valid & ~sky, device=dev); return f, base, feats(f, base)
bands = {"top": (0, h // 4), "mid": (h // 4, h // 2), "low": (h // 2, 3 * h // 4), "bot": (3 * h // 4, h), "all": (0, h)}
def band_losses(ren, alpha, tgt, base):
    out = {}
    for b, (y0, y1) in bands.items():
        m = torch.zeros_like(base); m[y0:y1] = base[y0:y1]; out[b] = loss(ren, alpha, tgt, m, base)
    return out
with torch.no_grad():
    if os.environ.get("YAW"):
        i = int(os.environ["YAW"]); p = PI[i]; f, base, tgt = dvr(i)
        R0 = qR(p["quat"]); C = np.array(p["pos"])
        BL = float(os.environ.get("BLUR", 0))
        if BL:
            import torch.nn.functional as F_
            k = int(BL * 3) * 2 + 1; g1 = torch.exp(-(torch.arange(k, device=dev) - k // 2) ** 2 / (2 * BL * BL)); g1 /= g1.sum()
            def blur(im): x = im.permute(2, 0, 1)[None]; x = F_.conv2d(x, g1.view(1, 1, 1, k).expand(3, 1, 1, k), padding=(0, k // 2), groups=3); x = F_.conv2d(x, g1.view(1, 1, k, 1).expand(3, 1, k, 1), padding=(k // 2, 0), groups=3); return x[0].permute(1, 2, 0)
            f = blur(f); tgt = feats(f, base)
        print(f"yaw sweep on #{i} (blur {BL}): deg  top  mid  low  bot  all")
        for dy in range(-6, 7):
            a = math.radians(dy); Ry = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
            q = {"quat": p["quat"], "pos": p["pos"]}
            R = R0 @ Ry; w2c = np.eye(4); w2c[:3, :3] = R.T; w2c[:3, 3] = -R.T @ C
            img, alpha, _ = rasterization(means, quats, scales, opac, colors, torch.tensor(w2c, dtype=torch.float32, device=dev)[None], K, w, h, sh_degree=SH)
            ren = blur(img[0]) if BL else img[0]; bl = band_losses(ren, alpha[0, ..., 0], tgt, base)
            print(f"  {dy:+d}  " + "  ".join(f"{bl[b]:.3f}" for b in bands), flush=True)
            if DUMP and dy in (-3, 0, 3):
                cv2.imwrite(f"{DUMP}/yaw_{i}_{dy:+d}.jpg", cv2.cvtColor(np.concatenate([(f.cpu().numpy() * 255).astype(np.uint8), (img[0].clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)], axis=1), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
    else:
        idx = [int(x) for x in sys.argv[5].split(",")]; hist = {}
        print("   i   loss(all) at offset -2 -1 0 +1 +2   best")
        for i in idx:
            ri, ai = render(PI[i]); row = []
            for o in (-2, -1, 0, 1, 2):
                f, base, tgt = dvr(i + o); row.append(band_losses(ri, ai, tgt, base)["all"])
            b = int(np.nanargmin(row)) - 2; hist[b] = hist.get(b, 0) + 1
            print(f"{i:5d}  " + "  ".join(f"{x:.3f}" for x in row) + f"   {b:+d}", flush=True)
        print("best offset histogram:", dict(sorted(hist.items())))
