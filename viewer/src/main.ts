// JDL-2026-R6: the scan as gaussian splats, every camera that was ever solved against it
// (Avata 2 scan frames, HDZero DVR frames), and the two videos, so a render from any camera
// can be laid over the frame it was solved from. Frame: x east, y up, z south, metres.
import * as pc from 'playcanvas'

type Pose = { i: number; t: number; pos: number[]; quat: number[]; src: string } | null
type ScanCam = { name: string; clip: string; t: number; pos: number[]; quat: number[] }

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const canvas = $<HTMLCanvasElement>('c'), video = $<HTMLVideoElement>('video'), scanvideo = $<HTMLVideoElement>('scanvideo'), status = $('status')
const app = new pc.Application(canvas, { mouse: new pc.Mouse(canvas), keyboard: new pc.Keyboard(window), graphicsDeviceOptions: { antialias: false } })
app.setCanvasFillMode(pc.FILLMODE_NONE)
app.setCanvasResolution(pc.RESOLUTION_AUTO)
app.scene.ambientLight = new pc.Color(0.2, 0.2, 0.2)

// --- camera. COLMAP cameras look +z with y down; PlayCanvas cameras look -z with y up, so a
// solved pose becomes a PlayCanvas rotation by a half turn about the camera's own x axis.
const cam = new pc.Entity('cam')
cam.addComponent('camera', { clearColor: new pc.Color(0.03, 0.03, 0.04), fov: 60, nearClip: 0.2, farClip: 2000 })
app.root.addChild(cam)
// Compare mode shows three things: the free camera (cam, left half of the canvas), the render
// from the DVR pose (cam2, a 4:3 box in the right half) and the DVR frame wiped over that box.
// cam2 renders the World layer only, so the path, frusta and gate circles (Immediate layer) stay
// in the free view and never sit on top of the photo.
const cam2 = new pc.Entity('cam2')
cam2.addComponent('camera', { clearColor: new pc.Color(0.03, 0.03, 0.04), fov: 60, nearClip: 0.2, farClip: 2000, priority: 1,
  layers: [pc.LAYERID_WORLD], aspectRatioMode: pc.ASPECT_MANUAL, aspectRatio: 4 / 3 })
app.root.addChild(cam2); cam2.enabled = false
const X180 = new pc.Quat(1, 0, 0, 0)
const q = new pc.Quat(), tmpV = new pc.Vec3()
function applyPose(e: pc.Entity, pos: number[], quat: number[]) {
  q.set(quat[0], quat[1], quat[2], quat[3]).mul(X180)
  e.setPosition(pos[0], pos[1], pos[2]); e.setRotation(q)
}

// --- orbit control (drag: orbit, right/middle drag or shift: pan, wheel: dolly)
const orbit = { target: new pc.Vec3(10, 3, -10), yaw: -35, pitch: -25, dist: 90, enabled: true }
function applyOrbit() {
  if (!orbit.enabled) return
  const r = new pc.Quat().setFromEulerAngles(orbit.pitch, orbit.yaw, 0)
  const back = r.transformVector(new pc.Vec3(0, 0, orbit.dist), new pc.Vec3())
  cam.setPosition(back.add(orbit.target)); cam.setRotation(r); cam.camera!.fov = 60; cam.camera!.horizontalFov = false
}
let drag: { x: number; y: number; btn: number } | null = null
canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, btn: e.button }; canvas.setPointerCapture(e.pointerId) })
canvas.addEventListener('pointerup', () => { drag = null })
canvas.addEventListener('contextmenu', e => e.preventDefault())
canvas.addEventListener('pointermove', e => {
  if (!drag || !orbit.enabled) return
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY
  if (drag.btn === 0 && !e.shiftKey) { orbit.yaw -= dx * 0.3; orbit.pitch = Math.max(-89, Math.min(89, orbit.pitch - dy * 0.3)) }
  else {
    const r = new pc.Quat().setFromEulerAngles(orbit.pitch, orbit.yaw, 0), k = orbit.dist * 0.0015
    orbit.target.sub(r.transformVector(new pc.Vec3(dx * k, 0, 0), tmpV)).add(r.transformVector(new pc.Vec3(0, dy * k, 0), tmpV))
  }
})
canvas.addEventListener('wheel', e => { if (orbit.enabled) orbit.dist = Math.max(1, orbit.dist * Math.exp(e.deltaY * 0.001)); e.preventDefault() }, { passive: false })

// --- scene
const splat = new pc.Entity('scene')
// Three colour versions of the same splats; switching swaps the asset, nothing else changes.
const sceneSel = $<HTMLSelectElement>('scene')
sceneSel.value = new URLSearchParams(location.search).get('scene') ?? sceneSel.value
let asset: pc.Asset | null = null
function loadScene(name: string) {
  if (asset) { splat.removeComponent('gsplat'); app.assets.remove(asset); asset.unload(); asset = null }
  status.textContent = 'loading ' + name + '…'
  const a = new pc.Asset(name, 'gsplat', { url: `/data/scene/${name}.sog` }); asset = a
  app.assets.add(a)
  a.on('load', () => { if (asset !== a) return; splat.addComponent('gsplat', { asset: a }); if (!splat.parent) app.root.addChild(splat); status.textContent = name })
  a.on('error', (err: string) => { status.textContent = 'scene failed: ' + err })
  app.assets.load(a)
}
sceneSel.onchange = () => loadScene(sceneSel.value)
loadScene(sceneSel.value)

// --- data
let poses: Pose[] = [], scan: ScanCam[] = [], gates: Record<string, number[]> = {}
const DVR = { fx: 402.8, w: 960, h: 720, t0: 80, fps: 60 }       // from dvr_pinhole.mp4.json
const SCAN = { fx: 1048.44, fy: 1048.62, w: 2688, h: 2016, fps: 59.94 }
async function loadPoses(name: string) {
  const j = await fetch('/data/' + name).then(r => r.json()); poses = j.poses
  status.textContent = `${name}: ${poses.filter(Boolean).length} frames`
}
fetch('/data/scan_cameras.json').then(r => r.json()).then(j => { scan = j.cameras; gates = j.gates })
fetch('/data/dvr_pinhole.mp4.json').then(r => r.json()).then(j => { DVR.fx = j.fx; DVR.w = j.width; DVR.h = j.height; DVR.t0 = j.t0; DVR.fps = j.fps })
loadPoses('poses60_refined10.json')

// --- ui
const ui = { follow: $<HTMLInputElement>('follow'), compare: $<HTMLInputElement>('compare'), wipe: $<HTMLInputElement>('wipe'),
  showScan: $<HTMLInputElement>('showScan'), showPath: $<HTMLInputElement>('showPath'), seek: $<HTMLInputElement>('seek'),
  play: $<HTMLButtonElement>('play'), tlabel: $('tlabel'), poseset: $<HTMLSelectElement>('poseset'), scancam: $<HTMLInputElement>('scancam'), scanlabel: $('scanlabel') }
$<HTMLSelectElement>('rate').onchange = e => { video.playbackRate = Number((e.target as HTMLSelectElement).value) }
ui.play.onclick = () => { if (video.paused) video.play(); else video.pause(); ui.play.textContent = video.paused ? 'play' : 'pause' }
// The browser shows the frame whose timestamp is <= currentTime (floor); the pose index must use
// the same rule, or during playback the pose runs one frame ahead of the picture half the time
// (measured 2026-09-21: currentTime 3.075 showed frame 184 while round() picked pose #185, 3.8 deg
// off at 230 deg/s). Seeks land mid-frame so a float boundary cannot flip the picture.
const frameOf = (t: number) => Math.floor(t * DVR.fps + 1e-3)
ui.seek.oninput = () => { video.currentTime = (Number(ui.seek.value) + 0.5) / DVR.fps }
ui.poseset.onchange = () => loadPoses(ui.poseset.value)
ui.wipe.oninput = () => document.documentElement.style.setProperty('--wipe', ui.wipe.value + '%')
const orbitAllowed = () => Number(ui.scancam.value) < 0 && (ui.compare.checked || !ui.follow.checked)
ui.compare.onchange = () => { $('app').classList.toggle('compare', ui.compare.checked); if (ui.compare.checked) ui.follow.checked = true; orbit.enabled = orbitAllowed(); layout() }
ui.follow.onchange = () => { if (!ui.follow.checked && ui.compare.checked) { ui.compare.checked = false; $('app').classList.remove('compare') } orbit.enabled = orbitAllowed(); layout() }
ui.scancam.oninput = () => {
  const sc = Number(ui.scancam.value); orbit.enabled = orbitAllowed()
  const s = scan[sc]
  if (s) {                                        // the frame this scan camera was solved from
    const src = `/data/scan_${s.clip}.mp4`; if (!scanvideo.src.endsWith(src)) scanvideo.src = src
    scanvideo.currentTime = s.t + 1e-4; video.hidden = true; scanvideo.hidden = false
  } else { video.hidden = false; scanvideo.hidden = true }
  layout()
}
window.addEventListener('keydown', e => {
  if (e.target instanceof HTMLInputElement) return
  if (e.key === ' ') { ui.play.click(); e.preventDefault() }
  if (e.key === 'ArrowRight') video.currentTime += (e.shiftKey ? 10 : 1) / DVR.fps
  if (e.key === 'ArrowLeft') video.currentTime -= (e.shiftKey ? 10 : 1) / DVR.fps
  if (e.key === 'f') { ui.follow.checked = !ui.follow.checked; ui.follow.onchange!(new Event('change')) }
  if (e.key === 'c') { ui.compare.checked = !ui.compare.checked; ui.compare.onchange!(new Event('change')) }
})

// --- layout: the 3D canvas follows its box; in compare mode the video is laid exactly over it
const view = $('view'), dvr = $('dvr')
function layout() {
  // The grid cell is the source of truth; the canvas is absolutely placed inside it, so it never
  // holds the cell open. Whenever the 3D view renders from a solved camera (follow or a scan
  // camera) it takes a centred 4:3 box, so side by side it is the same size as the video frame;
  // in compare mode the video is laid over that same box.
  const cell = view.getBoundingClientRect()
  const fromCamera = ui.follow.checked || Number(ui.scancam.value) >= 0
  cam2.enabled = ui.compare.checked
  if (ui.compare.checked) {
    // the canvas covers the whole row; the free camera takes the left half, the matched camera a
    // centred 4:3 box in the right half, and the video is laid exactly over that box
    Object.assign(canvas.style, { left: '0px', top: '0px' })
    const W = Math.floor(cell.width), H = Math.floor(cell.height); app.resizeCanvas(W, H)
    const half = W / 2, w = Math.floor(Math.min(half, H * 4 / 3)), h = Math.floor(w * 3 / 4)
    const left = Math.floor(half + (half - w) / 2), top = Math.floor((H - h) / 2)
    cam.camera!.rect.set(0, 0, 0.5, 1); cam.camera!.scissorRect.set(0, 0, 0.5, 1)
    cam2.camera!.rect.set(left / W, (H - top - h) / H, w / W, h / H); cam2.camera!.scissorRect.copy(cam2.camera!.rect)
    Object.assign(dvr.style, { left: cell.left + left + 'px', top: cell.top + top + 'px', width: w + 'px', height: h + 'px', right: 'auto', bottom: 'auto' })
  } else if (fromCamera) {
    cam.camera!.rect.set(0, 0, 1, 1); cam.camera!.scissorRect.set(0, 0, 1, 1)
    const w = Math.floor(Math.min(cell.width, cell.height * 4 / 3)), h = Math.floor(w * 3 / 4)
    const left = Math.floor((cell.width - w) / 2), top = Math.floor((cell.height - h) / 2)
    Object.assign(canvas.style, { left: left + 'px', top: top + 'px' })
    app.resizeCanvas(w, h)
    Object.assign(dvr.style, { left: '', top: '', width: '', height: '', right: '', bottom: '' })
  } else {
    cam.camera!.rect.set(0, 0, 1, 1); cam.camera!.scissorRect.set(0, 0, 1, 1)
    Object.assign(canvas.style, { left: '0px', top: '0px' })
    Object.assign(dvr.style, { left: '', top: '', width: '', height: '', right: '', bottom: '' })
    app.resizeCanvas(Math.floor(cell.width), Math.floor(cell.height))
  }
}
new ResizeObserver(layout).observe(view); window.addEventListener('resize', layout)

// --- per frame
// path colour = how the frame's pose was obtained (src): COLMAP-registered, LK gap fill, interpolated, photometrically refined
const cSrc: Record<string, pc.Color> = { kept: new pc.Color(0.3, 1, 0.4), lk: new pc.Color(1, 0.6, 0.15), interp: new pc.Color(1, 0.3, 0.85), refined: new pc.Color(0.3, 0.85, 1), ground: new pc.Color(0.55, 0.55, 0.55) }
const cPath = cSrc.interp, cScan = new pc.Color(0.2, 0.75, 1), cNow = new pc.Color(1, 1, 0.3)
const pathPos: pc.Vec3[] = [], pathCol: pc.Color[] = []
function frustum(pos: number[], quat: number[], hfovDeg: number, aspect: number, len: number, col: pc.Color, out: pc.Vec3[], cols: pc.Color[]) {
  const r = new pc.Quat(quat[0], quat[1], quat[2], quat[3]); const o = new pc.Vec3(pos[0], pos[1], pos[2])
  const x = Math.tan(hfovDeg / 2 * Math.PI / 180) * len, y = x / aspect
  const corners = [[-x, -y, len], [x, -y, len], [x, y, len], [-x, y, len]].map(c => r.transformVector(new pc.Vec3(c[0], c[1], c[2]), new pc.Vec3()).add(o))
  for (let k = 0; k < 4; k++) { out.push(o, corners[k], corners[k], corners[(k + 1) % 4]); cols.push(col, col, col, col) }
}
const SRC_NAME: Record<string, string> = { kept: 'colmap', lk: 'lk', interp: 'interp', refined: 'refined', ground: 'ground' }
let lastPoses = poses
app.on('update', () => {
  const i = Math.max(0, Math.min(poses.length - 1, frameOf(video.currentTime)))
  ui.seek.value = String(i); const p = poses[i]
  ui.tlabel.textContent = `${(DVR.t0 + i / DVR.fps).toFixed(3)} s  #${i}  ${p ? SRC_NAME[p.src] ?? p.src : '-'}`
  const lines: pc.Vec3[] = [], cols: pc.Color[] = []
  if (ui.showPath.checked) {
    if (lastPoses !== poses) { pathPos.length = 0; pathCol.length = 0; lastPoses = poses
      let prev: Pose = null
      for (const x of poses) { if (x && prev) { pathPos.push(new pc.Vec3(prev.pos[0], prev.pos[1], prev.pos[2]), new pc.Vec3(x.pos[0], x.pos[1], x.pos[2])); const c = cSrc[x.src] ?? cPath; pathCol.push(c, c) } prev = x } }
    if (pathPos.length) app.drawLines(pathPos, pathCol, true)
  }
  if (ui.showScan.checked) for (const s of scan) frustum(s.pos, s.quat, 104, 4 / 3, 0.8, cScan, lines, cols)
  const sc = Number(ui.scancam.value)
  if (sc >= 0 && scan[sc]) {
    const s = scan[sc]; ui.scanlabel.textContent = `${s.name} @ ${s.t.toFixed(2)} s`
    applyPose(cam, s.pos, s.quat); cam.camera!.horizontalFov = false; cam.camera!.fov = 2 * Math.atan(SCAN.h / 2 / SCAN.fy) * 180 / Math.PI
  } else if (p && ui.follow.checked && !ui.compare.checked) {
    applyPose(cam, p.pos, p.quat); cam.camera!.horizontalFov = false; cam.camera!.fov = 2 * Math.atan(DVR.h / 2 / DVR.fx) * 180 / Math.PI
  } else applyOrbit()
  if (p && ui.compare.checked) { applyPose(cam2, p.pos, p.quat); cam2.camera!.horizontalFov = false; cam2.camera!.fov = 2 * Math.atan(DVR.h / 2 / DVR.fx) * 180 / Math.PI }
  if (p && !(ui.follow.checked && sc < 0 && !ui.compare.checked)) frustum(p.pos, p.quat, 100, 4 / 3, 3, cNow, lines, cols)
  if (lines.length) app.drawLines(lines, cols, true)
})
app.start(); layout()
