# HDZero DVR → 3DGS photometric refinement (hdz_0067, JDL-2026-R6)

Run on win4090 (WSL, RTX 4090, gsplat 1.5.3 / torch 2.11 cu128). Run 2: 2026-09-21 02:10–08:00 (superseded
by the runs below once the path resolver, the 60 fps grid Kalman and the LK gap fill changed the init).

**Input** `poses60_init.json`: every 60 fps frame from 80 s, interpolated (cubic Hermite + slerp)
through the 529 COLMAP-localized 10 fps frames after gate disambiguation and Kalman smoothing.
**Scene** `JDL-2026-R6-fix-web.ply` (fixed model, web frame: x east, y up, z south, metres).
**Target** `dvr_pinhole.mp4` (fisheye undistorted to a 100° pinhole, fx 402.8 @ 960×720).

**Per frame**: 6-DoF delta on the camera side (se(3)), Adam, 24 iterations coarse→fine
(240×180 then 480×360), SH degree 1, splats with opacity < 0.1 dropped (2.56 M left).
Loss = normalized-gray L1 + 2 × gradient L1, masked: OSD, undistortion border, and the sky
detected in the DVR frame (the scan's sky is floaters). Prior 0.1·((rot/6°)² + (trans/1 m)²);
a delta is accepted only if the loss falls ≥ 1 % and rot ≤ 10°, shift ≤ 1.5 m. 3.8 s/frame.

**Result**
- frames 5676, accepted 3611 (64%)
- accepted: rot p50 2.1 deg p90 4.3 max 7.7; shift p50 0.10 m p90 0.15 max 0.23; loss improvement p50 2.4% p90 5.4%
- by source: {'kept': '1072/1585', 'interp': '2539/4091'}

Accepted frames carry the refined pose; the rest are re-interpolated between accepted
neighbours (`refined2poses.py` → `poses60_refined.json`). Raw per-frame records: `refined.jsonl`.

**Lesson**: the first run seeded each frame from the previous frame's delta. That chain walked off —
median 15–59° after a few hundred frames while the loss kept falling, because grass gives the
photometric loss almost nothing to hold a pose against. Independent per-frame starts plus a
quadratic prior fixed it; a prior of 3°/0.5 m was too tight (corrections 0.3°, 9 % accepted),
6°/1 m is the setting used.

## Run 3 (2026-09-21 10:45–12:10) — the interpolated frames of the current init

`ONLY_INTERP=1 ITERS=16`, 2.77 s/frame. Only frames with no observation (`src: interp`, 2193);
observed frames kept their pose. Accepted 1270/2193 (58%): shift p50 0.07 m p90 0.11, rot p50 2.4°
p90 3.9°.

## Run 4 (2026-09-21 12:12–13:40) — the LK frames

`interp60.py` now labels a frame `kept` only when COLMAP registered it; a frame whose only
observation is the LK gap fill is `lk` (2034), and 14 LK measurements whose PnP returned the previous
frame's position (< 2 cm apart at 0.3 m/frame, runs of up to 4 at #184–#187) are dropped from the
Kalman input. An `lk` frame's heading is a slerp between COLMAP frames, its position a few-point PnP:
#184 was 3° of yaw and 0.4 m off with the far pylon 30 px and the near flag 65 px right of the render,
and run 3 had skipped it as observed. Run 4 refines `lk` + the 16 frames the dropped measurements
turned into `interp`, same settings, appending to the same `refined.jsonl` (its done-set skips run 3's
frames). `poses60_refined.json` is rebuilt from the full file afterwards.

Result: 2.55 s/frame; lk accepted 1193/1949 (61%), shift p50 0.07 m p90 0.11, rot p50 1.8° p90 3.7°.
#184: accepted, 3.35° / 4 cm, the yaw the render comparison had shown. Merged file: 2470 refined,
1413 colmap, 756 lk (rejected, init pose), 925 interp, 112 ground; one step > 1 m (1.22 m).

**Ground** (2026-09-21): the drone sits still until #116 (Saqoosha, from the DVR). `interp60.py` gives
#4–#115 one pose — median position of the 105 observations there (spread p50 0.35 m, one PnP 1.8 m
out), chordal-mean rotation of the COLMAP frames — labelled `ground`; refine skips it, the merge keeps it.

**Merge rule** (2026-09-21): a frame that is not accepted keeps its init pose when it was observed
(`kept`, `lk`) and also when the accepted neighbours are more than 0.5 s apart; Hermite between
accepted frames 1 s apart drew a 4 m bulge at #500–#540 where the Kalman track was fine.

## Speed

Already CUDA; the cost is 32 rasterize+backward passes of 2.56 M splats per frame at 240×180 /
480×360, which leaves the 4090 mostly idle. `refine_batch.py` renders B frames per `rasterization`
call (`viewmats [B,4,4]`, per-frame losses summed so Adam keeps frames independent) and reads the
video in order instead of seeking. Measured 2026-09-21 on the 4090:

| | s/frame | note |
|---|---|---|
| refine.py (run 3/4) | 2.77 / 2.55 | seeks the video per frame |
| refine_batch BATCH=1 | 2.39 | same answers as run 3 on 20 frames (loss_init identical, pos < 1 cm, rot < 0.2°) |
| BATCH=4 | 0.76 | |
| BATCH=8 | 0.44 | 74/96 accepted vs run 3's 73, mean loss 1.9295 vs 1.9313 |
| BATCH=16 | — | fills the 24 GB (per-camera per-splat intermediates for the backward, 2.56 M × 16) and stalls |
| BATCH=4 ITERS=8 | 0.39 | mean loss 1.9255, no worse than 16 iterations on these frames |

## Band-balanced loss (2026-09-21)

`eval_bands.py` splits the frame into four horizontal bands (whole-frame normalisation, band-wise
mean). With the plain pixel mean, run 3/4 improved the grass rows and worsened the horizon rows,
the only rows that pin the rotation: over 96 frames the `mid` band got worse in 66/96
(2.67 → 3.05) while `low`/`bot` improved (1.77 → 1.62, 1.97 → 1.80); at #182–#188 the horizon went
3.1 → 4.0. That is why the refined #184 put the pylon further from the DVR than the init did.
`BANDS=4` makes the loss the mean of the four band means: every band improves on average
(top 2.29 → 2.03, mid 2.67 → 2.62, low 1.77 → 1.74, bot 1.97 → 1.93), #184's horizon 3.30 → 2.87,
#1603's top 5.74 → 4.09. Acceptance 66/96 instead of 74/96.

## Run 5 (2026-09-21 14:30–) — everything that is not COLMAP or ground

`refine_batch.py`, `BATCH=8 ITERS=16 BANDS=4 ONLY_INTERP=1` on the current init (interp + lk, 4151
frames) into `refined5.jsonl`; merged with `refined2poses.py` into `poses60_refined5.json` (viewer:
"refined (bands)"). Accepted 2856/4151 (69%), shift p50 0.07 m p90 0.10, rot p50 2.2° p90 4.1°; no
step > 1 m. #184 accepted (2.1°, 8 cm), #1612 rejected. Took ~2 h because the benchmarks shared the GPU.

## Run 6 (2026-09-21 16:10–) — blurred loss

Same as run 5 plus `BLUR=3` → `refined6.jsonl` → `poses60_refined6.json`, 31 min at 0.44 s/frame.
Accepted 3854/4151 (93%), shift p50 0.09 m, rot p50 1.5°; no step > 1 m. Merged file: 3854 refined,
1413 colmap, 141 lk, 156 interp, 112 ground.

**Run 5 vs run 6**, 60 frames both accepted + #184/#1612/#553/#2338, blurred band loss (σ 3):

| | horizon band (top, 58 valid) | mid | all | worse-than-init frames (all) |
|---|---|---|---|---|
| run 5 | 2.00 → 1.60, worse in 11 | 1.16 → 1.22 | 0.995 → 0.987 | 17/64 |
| run 6 | 2.00 → 1.46, worse in 2 | 1.16 → 1.12 | 0.995 → 0.961 | 7/64 |

Run 5 still moved against the horizon on 11 frames and against the mid band on average; run 6
improves every band except the grass rows (0.81 → 0.81, 0.90 → 0.91, within noise). #1612: run 5
rejected it, run 6 accepted it at 1.4° and the DVR/render wipe lines up on the flag. The two runs
agree to 1.3° / 6.5 cm (p50) where both accepted. **Run 6 is the viewer's default ("refined (blur)")**;
`BLUR=3 BANDS=4 BATCH=8 ITERS=16` is the setting for the next DVR.

## Rolling shutter (not done)

At #553 the far building (top-left) and the gate (right) sit off while the centre matches: the pose
turns at 395°/s, so an 8–16 ms readout puts 3–6° between the top and bottom rows (15–30 px at the
edges at 9.6 px/°); at #102 (3°/s) the periphery lines up. Calibration is excluded (bundle adjustment
moved fx 0.5 %, reprojection 1.62→1.64 px). Plan: warp each DVR frame to global shutter with the
track's ω and one readout-time scalar τ fitted on fast-turn frames, then re-refine; 106.6–107.2 s
turns at 450–530°/s and should gain most.

## What the loss can and cannot see (2026-09-21, after run 5 started)

Two tests on the band loss (whole-frame normalisation, `eval_offset.py`):

- **Frame offset**: for 40 COLMAP frames turning > 150°/s, render pose i and score it against DVR
  frames i−2..i+2. The best offset was 0 in 11/40, ±1 in 11, ±2 in 18 — one frame is 3–8° of yaw
  there, so the unblurred loss barely distinguishes it. (Not an index offset: the pinhole mp4 has
  frame i at exactly i/60 s, ffprobe.)
- **Yaw sweep on #184** (±6° in 1° steps): unblurred, the horizon band is flat (3.23–3.39, no
  minimum) and the grass bands fall monotonically toward one side, which is where the pose drifts.
  With both images Gaussian-blurred (σ 3 px at 480×360) the horizon band is a bowl, minimum 2° from
  the init, and the grass bands are flat. The grass texture is finer than the blur; the flags, gates
  and tree line are not.

So the refiner needed BLUR, not more iterations: `refine_batch.py BLUR=3` blurs the DVR frame and
the render (differentiable, per level) before the loss. Feature matching between the render and the
DVR does not work as an alternative (`refine_pnp.py`): LK forward-backward survivors 0–23 of ~1500,
SIFT ratio-test matches 6–16 per frame — the render is too soft and the DVR too blurred.

**Viewer frame rule** (2026-09-21): the browser shows the frame whose timestamp ≤ currentTime, so
the pose index is `floor(t·60)` and a seek lands mid-frame; `round()` had the pose one frame ahead of
the picture half the time during playback.

## Filling the COLMAP gaps with the mapper (2026-09-21 16:25–17:25)

#1525–#1550 (Saqoosha: "interpolated are wrong") sat in a 0.73 s hole between COLMAP anchors #1511 and
#1555 through which the drone turned 158° and dipped 4 m; slerp/Kalman drew a straight, level line,
and no refinement can move a pose 30° (the descent basin is a few degrees; `gapchain.py`, chaining
per-frame refinement from both ends, closed with 40° / 3 m of error and was dropped). The 7 COLMAP
observations inside were scattered ±1.5 m and had been rejected. `image_registrator` only fits new
images to existing 3D points, and a frame that sees only grass never registered.

`gapmap.ps1`: `image_deleter` of the 258 DVR images the resolver had called outliers/relabels, then
`mapper --Mapper.fix_existing_frames 1` on top (`sparse_fix5` → `sparse_gap`), so new DVR frames
triangulate points between themselves. Registered 3925/3927 images in 60 min. But: 450 of the 3273
DVR lines were absurd (centres at 10⁵ m), 192 were duplicates of the same frame from another batch
dir, and the new frames in grass-only stretches (#770–#1039, #4315–#4428) were a few metres off in a
self-consistent way that dragged the resolver into rejecting the old good anchors next to them.

**Photometric gate**: render the DVR frame at the mapper's pose and at run 6's pose, blurred band
loss (`eval_bands.py BLUR=3`); a new anchor is kept when its loss ≤ 1.05 × run 6's. Of 947 newly
registered frames 681 passed (#1511–#1555: 40/42, ratio p50 0.85; #1940–#1990: 30/49; #770–#1039:
1/2, the rest 30–80 % worse). Old anchors are kept regardless. (A first pass of the gate rejected
everything: the candidate rotations had been converted with `Mz R Mz` instead of interp60's `Mz R`.)

Result: COLMAP anchors 1413 → 1886; the LK fill is merged back for frames with no accepted anchor;
holes with no observation ≥ 0.2 s: 81 (1585 frames) → 32 (574). Init positions moved p50 0.15 m,
> 3 m on 135 frames (inside the filled holes). New anchors at #1514–#1535 score 0.80–1.04 (anchor
p50 0.99), #1541–#1550 1.1–1.3 (p90 1.17).

## Run 7 (2026-09-21 17:53–18:23) — run 6's settings on the gap-filled init

`BLUR=3 BANDS=4 BATCH=8 ITERS=16 ONLY_INTERP=1` on `poses60_init7.json` → `refined7.jsonl` →
`poses60_refined7.json` (viewer default, "refined (colmap gaps)"). Accepted 3386/3678 (92 %), shift
p50 0.09 m, rot p50 1.5°; merged: 3386 refined, 1886 colmap, 151 lk, 141 interp, 112 ground; no step
> 1 m.

## Run 8 (2026-09-21 18:27–) — the COLMAP anchors themselves

`ONLY_SRC=kept`, same settings, `refined8.jsonl`, 14 min. Anchors had never been refined; the 6° / 1 m
prior keeps them near COLMAP. Accepted 1668/1886 (88 %): shift p50 0.09 m p90 0.13, rot p50 1.0° p90
2.2°, loss −3.3 % (p50). #553 was refused (loss did not fall 1 %: the rolling-shutter frame). Merge of
run 7 + run 8 → `poses60_refined8.json` (viewer default, "refined (all)"): 5054 refined, 218 colmap,
151 lk, 141 interp, 112 ground; no step > 1 m. Every frame with an observation has now been through
the blurred photometric refinement once; what is left is the rolling shutter at > 300°/s and the 32
holes with no observation (574 frames, interpolated).

## Run 9 (2026-09-21 19:45–19:58) — the anchors that the dedup had thrown away

Saqoosha: "#775–#990 orientation is worse than before." Cause: the mapper had registered the same
frame from several batch dirs (dvr/, dvr2/, dvr3/, dvr4/), and the per-frame dedup kept the line with
the most inliers — in #770–#994 that was the mapper's junk duplicate, which the gate then rejected,
while the run-6 anchor for the same frame was never a candidate. 102 run-6 anchors went that way;
in #770–#994 the 34 anchors vanished and the heading was slerped over 270 frames, up to 180° off
(positions barely moved: the LK fill held them). Fix: the run-6 anchors (1408) go in first, mapper
frames only where a frame has no old anchor (677 accepted), LK for the rest. Anchors 2009; #770–#994
heading now 0.0° from run 6's init. Run 9 = the 1619 frames whose init changed (`FRAMES=@changed9.txt`),
same settings: accepted 1479/1619 (91 %), shift p50 0.10 m, rot p50 1.2°. Merge = run 7+8 records for
the unchanged frames + run 9 → `poses60_refined9.json` (viewer default "refined (all)"): 5109 refined,
233 colmap, 119 lk, 103 interp, 112 ground; no step > 1 m; #770–#994 within 0.2° (p50) of run 6.

## The wrong-way hairpin at #1662–#1673 (2026-09-21, run 10)

Saqoosha: "#1650–#1685, wrong turn." Anchors #1662 (yaw −53°) and #1673 (141°) are 160° apart with
nothing observed between; slerp takes the short arc and the drone went the long way (200°). Three
things were tried before the answer:

- **LK rotations**: stale (−53° for 5 frames, then 140°): the LK PnP keeps its anchor's heading.
- **Visual gyro** (`dvr_gyro.py`): frame-to-frame rotation from the DVR alone. Essential matrix and a
  rotation-only homography on far points both drift ~5°/frame over the near grass (0.3 m/frame at
  10 m reads as 1–3° of rotation). With the scan's rendered depth (`gyro_pnp.py`, back-project +
  PnP) the closure over 409 ordinary gaps is p50 0.8° (0.14°/frame), but through this blurred
  900°/s hairpin the chain turned only 11° where the anchors say 160°, and the photometric check
  sided with the anchors (#1673–#1690: anchors 0.9–1.2, gyro 1.27–1.33). Frame-to-frame tracking
  is reliable exactly where slerp already is, and not in the turns. Kept as a tool, not applied.
- **Long arc** (`hairpin_arc.py`): the other geodesic (quaternion dot forced negative). Scored
  against the short arc with `eval_bands.py BLUR=3` on the 7 gaps with ≥ 100° between anchors:
  only #1662–#1673 prefers it (all-band 1.196 → 1.070, 9/10 frames); #600–#624, #910–#939,
  #2401–#2428, #3583–#3613, #4390–#4422, #5534–#5559 keep the short arc (0/23 … 9/29).

Run 10 = the 10 frames on the long arc, refined (all accepted, 2.3–6.3°, loss 2.20 → 1.50 at #1663,
1.04 → 0.91 at #1668). Merged with run 9 → `poses60_refined10.json` (viewer default "refined (all)").
