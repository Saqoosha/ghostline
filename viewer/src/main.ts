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
loadPoses('poses60_refined15.json')

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
  if (mk.mode.checked && marks.landmarks[mk.lm.value]?.pos) {          // nudge the selected landmark: arrows on the ground, PageUp/Down in height
    const lm = marks.landmarks[mk.lm.value]; const st = e.shiftKey ? 1 : 0.1; const p = lm.pos!
    const mv: Record<string, number[]> = { ArrowLeft: [-st, 0, 0], ArrowRight: [st, 0, 0], ArrowUp: [0, 0, -st], ArrowDown: [0, 0, st], PageUp: [0, st, 0], PageDown: [0, -st, 0] }
    if (mv[e.key]) { lm.pos = p.map((x, k) => Math.round((x + mv[e.key][k]) * 100) / 100); delete lm.rays; marksSave(); e.preventDefault(); return }
  }
  if (e.key === ' ') { ui.play.click(); e.preventDefault() }
  if (e.key === 'ArrowRight') video.currentTime += (e.shiftKey ? 10 : 1) / DVR.fps
  if (e.key === 'ArrowLeft') video.currentTime -= (e.shiftKey ? 10 : 1) / DVR.fps
  if (e.key === 'f') { ui.follow.checked = !ui.follow.checked; ui.follow.onchange!(new Event('change')) }
  if (e.key === 'c') { ui.compare.checked = !ui.compare.checked; ui.compare.onchange!(new Event('change')) }
  if (e.key === 'm') { mk.mode.checked = !mk.mode.checked; mk.mode.onchange!(new Event('change')) }

  if (/^[1-9]$/.test(e.key) && marks.landmarks[e.key]) mk.lm.value = e.key
})

// --- marks: human correspondences. A mark is a pixel on a DVR frame (pinhole coordinates) tagged with
// a landmark id (a flag base, a gate foot). The solver (tools/dvr/marks_solve.py) triangulates each
// landmark from marks on well-posed frames and then solves the badly-posed frames from their marks;
// the page only collects them and draws, for the current pose, where each known landmark projects.
type Mark = { i: number; id: string; u: number; v: number }
type Marks = { landmarks: Record<string, { name: string; pos?: number[]; rays?: number[][] }>; marks: Mark[] }
let marks: Marks = { landmarks: {}, marks: [] }
const mk = { mode: $<HTMLInputElement>('markmode'), lm: $<HTMLSelectElement>('landmark'), newlm: $<HTMLButtonElement>('newlm'), undo: $<HTMLButtonElement>('undomark'), info: $('markinfo'), canvas: $<HTMLCanvasElement>('marks') }
function lmRefresh() {
  const cur = mk.lm.value; mk.lm.innerHTML = ''
  for (const id of Object.keys(marks.landmarks)) { const o = document.createElement('option'); o.value = id; o.textContent = `${id} ${marks.landmarks[id].name}`.trim(); mk.lm.appendChild(o) }
  if (cur && marks.landmarks[cur]) mk.lm.value = cur
}
async function marksLoad() { try { marks = await fetch('/api/marks').then(r => r.json()) } catch { } lmRefresh() }
async function marksSave() { await fetch('/api/marks', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(marks, null, 1) }) }
marksLoad()
mk.mode.onchange = () => { $('app').classList.toggle('marking', mk.mode.checked) }
mk.newlm.onclick = () => { const n = Object.keys(marks.landmarks).length + 1; const id = String(n); const name = prompt('landmark name (e.g. flag purple, gate B left foot)', `lm${n}`) ?? `lm${n}`; marks.landmarks[id] = { name }; lmRefresh(); mk.lm.value = id; marksSave() }
mk.undo.onclick = () => { marks.marks.pop(); marksSave() }
// the video keeps 4:3 inside its box (object-fit: contain), so the picture rect is the largest 4:3 box centred in the element
function pictureRect() {
  const r = video.getBoundingClientRect(); const w = Math.min(r.width, r.height * 4 / 3), h = w * 3 / 4
  return { left: r.left + (r.width - w) / 2, top: r.top + (r.height - h) / 2, w, h }
}
mk.canvas.addEventListener('pointerdown', e => {
  if (!mk.mode.checked) return
  const id = mk.lm.value; if (!id) { alert('add a landmark first (+ new)'); return }
  const pr = pictureRect(); const u = (e.clientX - pr.left) / pr.w * DVR.w, v = (e.clientY - pr.top) / pr.h * DVR.h
  if (u < 0 || v < 0 || u > DVR.w || v > DVR.h) return
  const i = frameOf(video.currentTime)
  marks.marks = marks.marks.filter(m => !(m.i === i && m.id === id)); marks.marks.push({ i, id, u: Math.round(u * 10) / 10, v: Math.round(v * 10) / 10 }); marksSave()
  e.preventDefault()
})
// A click in the free 3D view (mark mode, no drag) puts the selected landmark on the ground plane
// under the cursor: the ray through the pixel meets y = groundY. The field is flat enough that the
// take-off ground level (camera y 1.58 sitting on the grass) serves the whole course; the box lets
// it be changed. The solver keeps a landmark that already has a position and only triangulates
// the ones without.
const groundY = $<HTMLInputElement>('groundy')
let press: { x: number; y: number } | null = null
canvas.addEventListener('pointerdown', e => { press = { x: e.clientX, y: e.clientY } })
canvas.addEventListener('pointerup', e => {
  if (!press || !mk.mode.checked || !orbit.enabled) { press = null; return }
  const moved = Math.hypot(e.clientX - press.x, e.clientY - press.y); press = null
  if (moved > 4) return
  const id = mk.lm.value; if (!id) { alert('add a landmark first (+ new)'); return }
  const r = canvas.getBoundingClientRect(); const sx = e.clientX - r.left, sy = e.clientY - r.top
  const c = cam.camera!; const rect = c.rect
  if (sx > r.width * (rect.x + rect.z) || sy > r.height * (1 - rect.y)) return          // outside the free view's rect (compare mode)
  const near = c.screenToWorld(sx, sy, c.nearClip, new pc.Vec3()), far = c.screenToWorld(sx, sy, c.farClip, new pc.Vec3())
  const d = far.clone().sub(near).normalize()
  // One click is a ray; the depth along it is a guess (the ground plane). A second click from a
  // different viewpoint replaces the guess by the closest point of the rays; more rays average.
  const lm = marks.landmarks[id]; lm.rays = (lm.rays ?? []).filter(r => Math.abs(new pc.Vec3(r[3], r[4], r[5]).dot(d)) < Math.cos(8 * Math.PI / 180))   // drop rays within 8 deg of this one (a re-click from the same place)
  lm.rays.push([near.x, near.y, near.z, d.x, d.y, d.z])
  if (lm.rays.length >= 2) {
    const A = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], b = [0, 0, 0]
    for (const r of lm.rays) { const o = r.slice(0, 3), v = r.slice(3, 6)
      for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) { const m = (i === j ? 1 : 0) - v[i] * v[j]; A[i][j] += m; b[i] += m * o[j] } }
    const X = solve3(A, b); if (X) lm.pos = X.map(x => Math.round(x * 100) / 100)
  } else {
    const gy = Number(groundY.value); if (Math.abs(d.y) < 1e-6) return
    const t = (gy - near.y) / d.y; if (t < 0) return
    const X = near.clone().add(d.mulScalar(t)); lm.pos = [Math.round(X.x * 100) / 100, Math.round(gy * 100) / 100, Math.round(X.z * 100) / 100]
  }
  marksSave()
})
function solve3(A: number[][], b: number[]): number[] | null {     // Cramer's rule, 3x3
  const det = (m: number[][]) => m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
  const D = det(A); if (Math.abs(D) < 1e-9) return null
  return [0, 1, 2].map(k => det(A.map((row, i) => row.map((v, j) => j === k ? b[i] : v))) / D)
}
$<HTMLButtonElement>('resetlm').onclick = () => { const lm = marks.landmarks[mk.lm.value]; if (lm) { delete lm.pos; delete lm.rays; marksSave() } }
// project a world point with the DVR camera (COLMAP axes: x right, y down, z forward)
function projectDvr(p: Pose, X: number[]): [number, number] | null {
  const q = new pc.Quat(p!.quat[0], p!.quat[1], p!.quat[2], p!.quat[3]).invert()
  const d = q.transformVector(new pc.Vec3(X[0] - p!.pos[0], X[1] - p!.pos[1], X[2] - p!.pos[2]), new pc.Vec3())
  if (d.z <= 0.1) return null
  return [DVR.fx * d.x / d.z + DVR.w / 2, DVR.fx * d.y / d.z + DVR.h / 2]
}
function drawMarks(i: number, p: Pose) {
  const pr = pictureRect(); const dr = dvr.getBoundingClientRect(); const c = mk.canvas
  Object.assign(c.style, { left: pr.left - dr.left + 'px', top: pr.top - dr.top + 'px', width: pr.w + 'px', height: pr.h + 'px' })
  const W = Math.round(pr.w * devicePixelRatio), H = Math.round(pr.h * devicePixelRatio)
  if (c.width !== W || c.height !== H) { c.width = W; c.height = H }
  const g = c.getContext('2d')!; g.clearRect(0, 0, W, H); const sx = W / DVR.w, sy = H / DVR.h
  g.lineWidth = 2 * devicePixelRatio; g.font = `${12 * devicePixelRatio}px ui-monospace, monospace`
  for (const m of marks.marks) if (m.i === i) {       // the human's mark: yellow ring
    g.strokeStyle = '#ffe14d'; g.fillStyle = '#ffe14d'; g.beginPath(); g.arc(m.u * sx, m.v * sy, 7 * devicePixelRatio, 0, Math.PI * 2); g.stroke(); g.fillText(m.id, m.u * sx + 9 * devicePixelRatio, m.v * sy - 6 * devicePixelRatio)
  }
  if (p) for (const id in marks.landmarks) {          // where the current pose says the landmark is: cyan cross
    const X = marks.landmarks[id].pos; if (!X) continue; const uv = projectDvr(p, X); if (!uv) continue
    const [u, v] = [uv[0] * sx, uv[1] * sy]; if (u < 0 || v < 0 || u > W || v > H) continue
    const r = 7 * devicePixelRatio; g.strokeStyle = '#4dd9ff'; g.fillStyle = '#4dd9ff'; g.beginPath(); g.moveTo(u - r, v); g.lineTo(u + r, v); g.moveTo(u, v - r); g.lineTo(u, v + r); g.stroke(); g.fillText(id, u + 9 * devicePixelRatio, v + 14 * devicePixelRatio)
  }
  const n = marks.marks.filter(m => m.i === i).length, tot = marks.marks.length, fr = new Set(marks.marks.map(m => m.i)).size
  mk.info.textContent = `${n} on this frame · ${tot} marks on ${fr} frames`
}

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
const cSrc: Record<string, pc.Color> = { kept: new pc.Color(0.3, 1, 0.4), lk: new pc.Color(1, 0.6, 0.15), interp: new pc.Color(1, 0.3, 0.85), refined: new pc.Color(0.3, 0.85, 1), ground: new pc.Color(0.55, 0.55, 0.55), manual: new pc.Color(1, 0.88, 0.3) }
const cPath = cSrc.interp, cScan = new pc.Color(0.2, 0.75, 1), cNow = new pc.Color(1, 1, 0.3), cLm = new pc.Color(1, 0.88, 0.3)
const pathPos: pc.Vec3[] = [], pathCol: pc.Color[] = []
function frustum(pos: number[], quat: number[], hfovDeg: number, aspect: number, len: number, col: pc.Color, out: pc.Vec3[], cols: pc.Color[]) {
  const r = new pc.Quat(quat[0], quat[1], quat[2], quat[3]); const o = new pc.Vec3(pos[0], pos[1], pos[2])
  const x = Math.tan(hfovDeg / 2 * Math.PI / 180) * len, y = x / aspect
  const corners = [[-x, -y, len], [x, -y, len], [x, y, len], [-x, y, len]].map(c => r.transformVector(new pc.Vec3(c[0], c[1], c[2]), new pc.Vec3()).add(o))
  for (let k = 0; k < 4; k++) { out.push(o, corners[k], corners[k], corners[(k + 1) % 4]); cols.push(col, col, col, col) }
}
const SRC_NAME: Record<string, string> = { kept: 'colmap', lk: 'lk', interp: 'interp', refined: 'refined', ground: 'ground', manual: 'manual' }
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
  for (const id in marks.landmarks) {                 // known landmarks: a short vertical tick with its id's colour
    const X = marks.landmarks[id].pos; if (!X) continue
    lines.push(new pc.Vec3(X[0], X[1], X[2]), new pc.Vec3(X[0], X[1] + 3, X[2])); cols.push(cLm, cLm)
  }
  if (lines.length) app.drawLines(lines, cols, true)
  drawMarks(i, p)
})
app.start(); layout()
