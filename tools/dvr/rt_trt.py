"""LighterGlue and XFeat as TensorRT engines for rt_track.py (GLUE=<engine>, XFEAT=<engine>).

The tracker runs both with fixed sizes except the batch - XFeat on 640 x 480 frames keeping TOPK (2048) points, the
matcher on both sides padded to TOPK - which is what TensorRT wants.

glue   the whole matcher: keypoint normalization, the 6 transformer layers, the last layer's assignment and the
       mutual-nearest / threshold test -> per query point the matched map point (-1 = none) and its score. The same as
       kornia's LightGlue with width_confidence -1 (no pruning) and depth_confidence -1 (no early stop), as rt_track.py runs it.
xfeat  the network and detectAndCompute's sparse keypoints: NMS, reliability score, top TOPK, bicubic descriptors. Its
       variable-length steps (nonzero over the NMS mask, argsort over the candidates) become a score for every pixel,
       -1 where NMS or the threshold drops it, then topk: the same points, ties perhaps in another order. Points past the
       last candidate have score -1 (detectAndCompute drops score <= 0; so must the caller).

TensorRT 11 builds strongly typed networks only (no FP16 builder flag): the precision is the ONNX graph's own, so the
variants are exported in the dtype wanted.
  glue_fp32 / glue_mix / glue_fp16   mix = the transformer layers float16, the assignment (two log-softmaxes over 2048, the mutual test) float32
  xfeat_fp32 / xfeat_fp16            fp16 = the CNN float16, the keypoint selection and descriptor sampling float32

usage (mastenv + tensorrt-cu12, ~/xfeat):
  rt_trt.py build <out_dir> [glue_mix xfeat_fp16 ...]       -> <out_dir>/<variant>.onnx / .engine (all variants by default)
  rt_trt.py bench-glue map.npz <engine> [<engine> ...]       -> agreement with the PyTorch matcher and time per batch
  rt_trt.py bench-xfeat video.mp4 <engine> [<engine> ...]    -> agreement with PyTorch detectAndCompute and time per batch
env: TOPK (2048), BMAX (16) the largest matcher batch, XBMAX (8) the largest XFeat batch, TH (0.1) match threshold,
     XFEAT_DIR (~/xfeat)"""
import os, sys, time, numpy as np, torch
from torch import nn
import torch.nn.functional as F

E = os.environ.get
TOPK, BMAX, XBMAX, TH = int(E("TOPK", 2048)), int(E("BMAX", 16)), int(E("XBMAX", 8)), float(E("TH", 0.1))
sys.path.insert(0, os.path.expanduser(E("XFEAT_DIR", "~/xfeat")))


def load_lighterglue():                             # the matcher as rt_track.py builds it
    from modules.lighterglue import LighterGlue
    LighterGlue.default_conf_xfeat["mp"] = False; LighterGlue.default_conf_xfeat["width_confidence"] = -1
    return LighterGlue().eval()


def load_xfeat():
    from modules.xfeat import XFeat
    return XFeat(top_k=TOPK)


class Glue(nn.Module):
    """kornia LightGlue._forward without pruning or early stop, for fixed-size inputs; tdt = transformer dtype, adt = assignment dtype."""
    def __init__(self, net, size, tdt, adt):
        super().__init__()
        self.net, self.tdt, self.adt = net, tdt, adt
        net.transformers.to(tdt); net.input_proj.to(tdt); net.log_assignment[-1].to(adt)
        w, h = size; self.shift, self.scale = (w / 2, h / 2), max(w, h) / 2
        self.H = net.conf.num_heads; self.D = net.conf.descriptor_dim // self.H

    # kornia's posenc, SelfBlock and CrossBlock rewritten so every reshape has fixed sizes: TensorRT refuses to parse
    # their unflatten(-1, (heads, -1, 3)) (two unknown axes) and cannot prove repeat_interleave's output broadcastable
    def posenc(self, k):                            # [B, N, 2] -> (cos, sin) [2, B, 1, N, D], each value twice in a row
        p = self.net.posenc.Wr(k)
        return torch.stack([torch.stack([c, c], -1).flatten(start_dim=-2) for c in (torch.cos(p), torch.sin(p))], 0).unsqueeze(-3).to(self.tdt)

    def self_block(self, b, x, enc):
        qkv = b.Wqkv(x).unflatten(-1, (self.H, self.D, 3)).transpose(1, 2)
        q, k, v = qkv[..., 0], qkv[..., 1], qkv[..., 2]
        rot = lambda t: (t * enc[0]) + (torch.stack((-t[..., 1::2], t[..., 0::2]), -1).flatten(start_dim=-2) * enc[1])
        ctx = F.scaled_dot_product_attention(rot(q), rot(k), v)
        return x + b.ffn(torch.cat([x, b.out_proj(ctx.transpose(1, 2).flatten(start_dim=-2))], -1))

    def cross_block(self, b, x0, x1):
        h = lambda t: t.unflatten(-1, (self.H, self.D)).transpose(1, 2)
        qk0, qk1, v0, v1 = h(b.to_qk(x0)), h(b.to_qk(x1)), h(b.to_v(x0)), h(b.to_v(x1))
        m0, m1 = F.scaled_dot_product_attention(qk0, qk1, v1), F.scaled_dot_product_attention(qk1, qk0, v0)
        m0, m1 = (b.to_out(t.transpose(1, 2).flatten(start_dim=-2)) for t in (m0, m1))
        return x0 + b.ffn(torch.cat([x0, m0], -1)), x1 + b.ffn(torch.cat([x1, m1], -1))

    def forward(self, kp0, d0, kp1, d1):
        sh = kp0.new_tensor(self.shift)
        k0, k1 = (kp0 - sh) / self.scale, (kp1 - sh) / self.scale
        e0, e1 = self.posenc(k0), self.posenc(k1)
        x0, x1 = self.net.input_proj(d0.to(self.tdt)), self.net.input_proj(d1.to(self.tdt))
        for layer in self.net.transformers:
            x0, x1 = self.self_block(layer.self_attn, x0, e0), self.self_block(layer.self_attn, x1, e1)
            x0, x1 = self.cross_block(layer.cross_attn, x0, x1)
        la = self.net.log_assignment[-1]; x0, x1 = x0.to(self.adt), x1.to(self.adt)
        m0, m1 = la.final_proj(x0), la.final_proj(x1); d = m0.shape[-1]
        sim = torch.einsum("bmd,bnd->bmn", m0 / d**0.25, m1 / d**0.25)
        # sigmoid_log_double_softmax without the dustbin row and column: filter_matches never reads them
        s = F.log_softmax(sim, 2) + F.log_softmax(sim, 1) + F.logsigmoid(la.matchability(x0)) + F.logsigmoid(la.matchability(x1)).transpose(1, 2)
        v0, i0 = s.max(2); i1 = s.max(1).indices
        mutual = torch.arange(s.shape[1], device=s.device)[None] == i1.gather(1, i0)
        sc = torch.where(mutual, v0.float().exp(), torch.zeros_like(v0, dtype=torch.float32))
        return torch.where(mutual & (sc > TH), i0, torch.full_like(i0, -1)).int(), sc


class InstanceNorm(nn.Module):                      # nn.InstanceNorm2d(affine=False) written out: its ONNX export makes its constants on the CPU and fails on cuda
    def forward(self, x):
        m = x.mean((2, 3), keepdim=True); return (x - m) / torch.sqrt(((x - m) ** 2).mean((2, 3), keepdim=True) + 1e-5)


class XF(nn.Module):
    """XFeat.detectAndCompute for a fixed frame size and TOPK points: frames [B, 3, H, W] in 0..1 -> keypoints [B, TOPK, 2] (x, y),
    descriptors [B, TOPK, 64], scores [B, TOPK] (-1 = no point)"""
    def __init__(self, net, size, dt, th=0.05):
        super().__init__()
        W, H = size; assert W % 32 == 0 and H % 32 == 0, "detectAndCompute would resize the frame"
        net.norm = InstanceNorm()
        for m in net.modules():                     # BatchNorm2d(affine=False): the export folds it into the conv with weights it makes on the CPU
            if isinstance(m, nn.BatchNorm2d) and m.weight is None:
                m.weight, m.bias, m.affine = nn.Parameter(torch.ones_like(m.running_mean)), nn.Parameter(torch.zeros_like(m.running_mean)), True
        self.net, self.dt, self.W, self.H, self.th = net.to(dt), dt, W, H, th
        ys, xs = torch.meshgrid(torch.arange(H), torch.arange(W), indexing="ij")
        # every pixel as InterpolateSparse2d normalizes a keypoint (x / (W - 1), with align_corners False in grid_sample)
        self.register_buffer("grid", torch.stack([2 * xs / (W - 1) - 1, 2 * ys / (H - 1) - 1], -1)[None].float())
        keep = torch.ones(1, H * W, dtype=torch.bool); keep[0, 0] = False   # detectAndCompute drops a point at (0, 0) as padding
        self.register_buffer("keep", keep)
        self.register_buffer("norm", torch.tensor([W - 1, H - 1]).float())

    def forward(self, x):
        M1, K1, H1 = self.net(x.to(self.dt)); M1, K1, H1 = F.normalize(M1.float(), dim=1), K1.float(), H1.float()
        B, h, w = x.shape[0], self.H // 8, self.W // 8
        K1h = F.softmax(K1, 1)[:, :64].permute(0, 2, 3, 1).reshape(B, h, w, 8, 8).permute(0, 1, 3, 2, 4).reshape(B, 1, self.H, self.W)
        g = self.grid.expand(B, -1, -1, -1)
        near = F.grid_sample(K1h, g, mode="nearest", align_corners=False); rel = F.grid_sample(H1, g, mode="bilinear", align_corners=False)
        cand = (K1h == F.max_pool2d(K1h, 5, stride=1, padding=2)) & (K1h > self.th)
        score = torch.where(cand, near * rel, torch.full_like(near, -1)).flatten(1)
        score, idx = torch.where(self.keep, score, torch.full_like(score, -1)).topk(TOPK, dim=1)
        kp = torch.stack([idx % self.W, torch.div(idx, self.W, rounding_mode="floor")], -1).float()
        d = F.grid_sample(M1, (2 * kp / self.norm - 1).unsqueeze(-2), mode="bicubic", align_corners=False)
        return kp, F.normalize(d.permute(0, 2, 3, 1).squeeze(-2), dim=-1), score


GLUE_DT = {"fp32": (torch.float32, torch.float32), "mix": (torch.float16, torch.float32), "fp16": (torch.float16, torch.float16)}
XFEAT_DT = {"fp32": torch.float32, "fp16": torch.float16}
VARIANTS = [f"glue_{v}" for v in GLUE_DT] + [f"xfeat_{v}" for v in XFEAT_DT]


def engine(module, args, ins, outs, shapes, path):  # shapes: per input (min, opt, max)
    import tensorrt as trt
    torch.onnx.export(module, args, path + ".onnx", input_names=ins, output_names=outs, opset_version=17, dynamo=False,
                      dynamic_axes={n: {0: "B"} for n in ins + outs})
    log = trt.Logger(trt.Logger.WARNING); b = trt.Builder(log)
    net = b.create_network(1 << int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED)); p = trt.OnnxParser(net, log)
    assert p.parse_from_file(path + ".onnx"), [p.get_error(i).desc() for i in range(p.num_errors)]
    cfg = b.create_builder_config(); prof = b.create_optimization_profile()
    for n in ins: prof.set_shape(n, *shapes[n])
    cfg.add_optimization_profile(prof); t = time.time()
    open(path + ".engine", "wb").write(b.build_serialized_network(net, cfg))
    print(f"{os.path.basename(path)}: built in {time.time() - t:.0f} s", flush=True)


def build(out, variants, size=(640, 480)):
    os.makedirs(out, exist_ok=True); W, H = size; c = "cuda"
    for v in variants:
        kind, dt = v.split("_")
        if kind == "glue":
            names = ["kp0", "d0", "kp1", "d1"]; sh = lambda C: [(1, TOPK, C), (8, TOPK, C), (BMAX, TOPK, C)]
            x = (torch.rand(2, TOPK, 2, device=c) * H, torch.randn(2, TOPK, 64, device=c), torch.rand(2, TOPK, 2, device=c) * H, torch.randn(2, TOPK, 64, device=c))
            engine(Glue(load_lighterglue().net.cuda(), size, *GLUE_DT[dt]).eval(), x, names, ["m0", "s0"], dict(zip(names, map(sh, (2, 64, 2, 64)))), f"{out}/{v}")
        else:
            x = torch.rand(2, 3, H, W, device=c)
            engine(XF(load_xfeat().net.cuda().eval(), size, XFEAT_DT[dt]).cuda().eval(), (x,), ["x"], ["kp", "desc", "score"],
                   {"x": [(1, 3, H, W), (4, 3, H, W), (XBMAX, 3, H, W)]}, f"{out}/{v}")


class Trt:
    """an engine as a function of cuda tensors (batch first) -> its outputs, in the order the engine lists them"""
    def __init__(self, path):
        import tensorrt as trt
        self.engine = trt.Runtime(trt.Logger(trt.Logger.WARNING)).deserialize_cuda_engine(open(path, "rb").read())
        self.ctx = self.engine.create_execution_context()
        names = [self.engine.get_tensor_name(i) for i in range(self.engine.num_io_tensors)]
        io = lambda n: self.engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT
        self.ins, self.outs = [n for n in names if io(n)], [n for n in names if not io(n)]
        dt = {trt.float32: torch.float32, trt.float16: torch.float16, trt.int32: torch.int32, trt.int64: torch.int64}
        self.dt = {n: dt[self.engine.get_tensor_dtype(n)] for n in names}
        self.stream = torch.cuda.Stream()           # TensorRT adds synchronizations of its own on the default stream

    def __call__(self, *args):
        self.keep = [a.to(self.dt[n]).contiguous() for n, a in zip(self.ins, args)]   # alive until the stream has used them
        for n, a in zip(self.ins, self.keep): self.ctx.set_input_shape(n, tuple(a.shape)); self.ctx.set_tensor_address(n, a.data_ptr())
        out = [torch.empty(tuple(self.ctx.get_tensor_shape(n)), dtype=self.dt[n], device="cuda") for n in self.outs]
        for n, o in zip(self.outs, out): self.ctx.set_tensor_address(n, o.data_ptr())
        cur = torch.cuda.current_stream(); self.stream.wait_stream(cur)
        assert self.ctx.execute_async_v3(self.stream.cuda_stream)
        cur.wait_stream(self.stream)
        return out


def bench_glue(map_npz, engines):
    m = np.load(map_npz); kp = torch.from_numpy(m["kp"][:, :TOPK]).float().cuda(); desc = torch.from_numpy(m["desc"][:, :TOPK]).float().cuda()
    W, H = (int(v) for v in m["size"]); size = torch.tensor([[W, H]], device="cuda").float()
    # pairs of keyframes a few apart along a flight: views about as far apart as a frame and its nearest keyframes
    rng = np.random.default_rng(0); a = rng.integers(0, len(kp) - 3, 64); pairs = [(int(i), int(i) + int(rng.integers(1, 4))) for i in a]
    lg = load_lighterglue()

    def ref(i0, i1):                                # rt_track.py's call
        B = len(i0)
        with torch.no_grad():
            o = lg.net({"image0": {"keypoints": kp[i0], "descriptors": desc[i0], "image_size": size.expand(B, 2)},
                        "image1": {"keypoints": kp[i1], "descriptors": desc[i1], "image_size": size.expand(B, 2)}})
        return [x.cpu().numpy() for x in o["matches"]]

    def trt_run(g, i0, i1):
        m0, _ = g(kp[i0], desc[i0], kp[i1], desc[i1]); m0 = m0.cpu().numpy()
        return [np.stack([np.nonzero(r >= 0)[0], r[r >= 0]], 1) for r in m0]

    def timed(f, B, n=30):
        i0 = [p[0] for p in pairs[:B]]; i1 = [p[1] for p in pairs[:B]]
        for _ in range(5): f(i0, i1)
        torch.cuda.synchronize(); t = time.perf_counter()
        for _ in range(n): f(i0, i1)
        torch.cuda.synchronize(); return (time.perf_counter() - t) / n * 1000

    R = []
    for c in range(0, len(pairs), 8): R += ref([p[0] for p in pairs[c:c + 8]], [p[1] for p in pairs[c:c + 8]])
    print(f"PyTorch: matches per pair p50 {np.median([len(r) for r in R]):.0f}; ms at B 1 / 8 / 16: " +
          " / ".join(f"{timed(ref, B):.1f}" for B in (1, 8, 16)), flush=True)
    for e in engines:
        g = Trt(e); T = []
        for c in range(0, len(pairs), 8): T += trt_run(g, [p[0] for p in pairs[c:c + 8]], [p[1] for p in pairs[c:c + 8]])
        RT = [(r, t) for r, t in zip(R, T) if len(r)]   # keyframes with no points match nothing either way
        same = [len(set(map(tuple, r)) & set(map(tuple, t))) / len(set(map(tuple, r)) | set(map(tuple, t))) for r, t in RT]
        ratio = [len(t) / len(r) for r, t in RT]
        print(f"{os.path.basename(e)}: matches p50 {np.median([len(t) for t in T]):.0f} (x{np.median(ratio):.3f} of PyTorch, min x{min(ratio):.3f});"
              f" same matches (IoU) p50 {np.median(same):.3f} min {min(same):.3f}; ms at B 1 / 8 / 16: " +
              " / ".join(f"{timed(lambda i0, i1: trt_run(g, i0, i1), B):.1f}" for B in (1, 8, 16)), flush=True)


def bench_xfeat(video, engines, n=96):
    import cv2
    cap, fr = cv2.VideoCapture(video), []
    while len(fr) < n:
        ok, f = cap.read()
        if not ok: break
        fr.append(cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2RGB), (640, 480), interpolation=cv2.INTER_AREA))   # as rt_track.py reads a file
    X = torch.from_numpy(np.stack(fr[::max(1, len(fr) // n)])).cuda().permute(0, 3, 1, 2).float() / 255
    xf = load_xfeat()

    def ref(x):                                     # rt_track.py's call
        with torch.no_grad(): fs = xf.detectAndCompute(x, top_k=TOPK)
        return [(f["keypoints"].cpu().numpy(), f["descriptors"]) for f in fs]

    def trt_run(g, x):
        kp, d, sc = g(x); ok = (sc > 0).cpu().numpy(); kp = kp.cpu().numpy()
        return [(kp[b][ok[b]], d[b][torch.from_numpy(ok[b]).cuda()]) for b in range(len(x))]

    def timed(f, B, k=30):
        x = X[:B]
        for _ in range(5): f(x)
        torch.cuda.synchronize(); t = time.perf_counter()
        for _ in range(k): f(x)
        torch.cuda.synchronize(); return (time.perf_counter() - t) / k * 1000

    R = [r for c in range(0, len(X), 4) for r in ref(X[c:c + 4])]
    print(f"PyTorch: points per frame p50 {np.median([len(r[0]) for r in R]):.0f}; ms at B 1 / 4 / 8: " + " / ".join(f"{timed(ref, B):.1f}" for B in (1, 4, 8)), flush=True)
    for e in engines:
        g = Trt(e); T = [t for c in range(0, len(X), 4) for t in trt_run(g, X[c:c + 4])]; iou, cos = [], []
        for (rk, rd), (tk, td) in zip(R, T):
            ra = {tuple(p): i for i, p in enumerate(np.round(rk).astype(int))}; ta = {tuple(p): i for i, p in enumerate(np.round(tk).astype(int))}
            both = ra.keys() & ta.keys(); iou.append(len(both) / max(1, len(ra.keys() | ta.keys())))
            if both:
                i, j = zip(*[(ra[p], ta[p]) for p in both])
                cos.append(float((rd[list(i)].float() * td[list(j)].float()).sum(-1).median()))
        print(f"{os.path.basename(e)}: points p50 {np.median([len(t[0]) for t in T]):.0f}; same points (IoU) p50 {np.median(iou):.3f} min {min(iou):.3f};"
              f" descriptor cosine at the same points p50 {np.median(cos):.4f} min {min(cos):.4f}; ms at B 1 / 4 / 8: " +
              " / ".join(f"{timed(lambda x: trt_run(g, x), B):.1f}" for B in (1, 4, 8)), flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "build": build(sys.argv[2], sys.argv[3:] or VARIANTS)
    elif cmd == "bench-glue": bench_glue(sys.argv[2], sys.argv[3:])
    elif cmd == "bench-xfeat": bench_xfeat(sys.argv[2], sys.argv[3:])
