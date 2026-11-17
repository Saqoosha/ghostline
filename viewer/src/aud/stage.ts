// The audience pages' 3D (watch.ts live, heat.ts replay): the scan, the course line, a sphere, a trail and a name tag per
// pilot, and the camera - Orbit, Top, Free (aud/freecam.ts, as livepage.ts), Follow one pilot. Drawn as livepage.ts draws them
// (that page is the venue's; this one leaves out its BA switch and sliders). A pilot's colour is their cell's place in the grid (--tl ... --br, look.css).
import * as pc from 'playcanvas'
import { L } from './i18n'
import { loadHQ, pickScan, saveHQ, sceneBlob } from './scan'
import { bakeSky } from '../sky'
import { insetPolygon, RoomOcclusion, type Room } from '../occlude'
import { FreeCam } from './freecam'
import { Cutaway } from './cutaway'

export type Slot = 'tl' | 'tr' | 'bl' | 'br'
export const SLOTS: Slot[] = ['tl', 'tr', 'bl', 'br']
export type Mode = 'orbit' | 'top' | 'free' | `p${string}`
export type PilotState = 'flying' | 'lost' | 'nopic'
// line: a flight's whole line only (compare.ts): thin and light all along, no sphere and no name tag
export interface StagePilot { stream: string; slot: Slot; color: string; pos: pc.Vec3 | null; line: boolean
  show(pos: number[], trail: (number[] | null)[], state: PilotState, label: { name: string; flag: string; note: string }): void; hide(): void }
export interface Stage { app: pc.Application; mode: Mode; onMode: (m: Mode) => void
  loadScene(progress?: (f: number) => void): Promise<{ title?: string }>; hq: boolean; setHQ(on: boolean): Promise<void>; setCourse(track: number[][] | null): void; pilot(stream: string, slot: Slot): StagePilot
  setMode(m: Mode): void; clear(): void
  toggleFollow(key: string): void   // follow that pilot, or back to the orbit when already following it
  showLine: boolean; setShowLine(on: boolean): void }

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()
const hex = (h: string) => new pc.Color(parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255)
// PULL: the trail, the line and the dots are drawn this far nearer the eye along the line of sight (the same place on screen):
// a splat is depth-tested at its centre alone, so a large floor splat whose centre is nearer than a line 1 m over the floor
// would cover it; an upper floor 5 m over it still does. Never more than half the way to the eye (Free flies close)
const pullOf = (dist: number) => Math.min(PULL, dist / 2)
const PULL = 1.5, TRAIL_PX = 5, LINE_W = 0.12, LINE_DASH = 1.0, LINE_GAP = 0.7, LINE_A = 0.3, LINE_XRAY = 0.15, TRAIL_XRAY = 0.35, PITCH0 = -18, TURN = 6, BLEND = 0.7
const cLost = new pc.Color(0.55, 0.57, 0.6)
// a phone gets the light scan (index.json "lite", tools/publish-event.sh) unless ?q=full; a tall screen looks down more steeply,
// or the orbit, backing off to hold the course across the narrow width, ends up inside the trees round it
const PHONE = typeof location !== 'undefined' && new URLSearchParams(location.search).get('q') !== 'full' && matchMedia('(pointer: coarse) and (max-width: 1000px) and (max-height: 1000px)').matches   // not a tablet
const PITCH_TALL = -38

function emptyMesh(m: pc.Mesh) { m.setPositions([0, 0, 0, 0, 0, 0, 0, 0, 0]); m.setNormals([0, 1, 0, 0, 1, 0, 0, 1, 0]); m.setColors(new Array(12).fill(0)); m.setIndices([0, 1, 2]); m.update(pc.PRIMITIVE_TRIANGLES) }

const SHOT = new URLSearchParams(typeof location === 'undefined' ? '' : location.search)   // (no location in the tests)
// the header's scan quality, apart from the cameras so it does not read as a fourth: LITE (0.8M splats) or HQ (the full 3M, 45 MB),
// the chosen one underlined. Redraw the buttons now, done once the new scan is in
export const hqButton = (stage: Stage, redraw: () => void, done: () => void) => {
  const q = (hq: boolean, label: string, title: string) => Object.assign(document.createElement('button'), { textContent: label, title, className: stage.hq === hq ? 'on' : '',
    onclick: () => { if (stage.hq !== hq) { stage.setHQ(hq).then(done, e => console.error('scan', e)); redraw() } } })
  const box = Object.assign(document.createElement('span'), { className: 'quality opt', title: L('スキャンの画質', 'Scan quality') })
  box.append(q(false, 'LITE', L('軽いスキャン（80 万 splat、12 MB）', 'Light scan (0.8M splats, 12 MB)')), q(true, 'HQ', L('高画質のスキャン（300 万 splat、45 MB）', 'High-quality scan (3M splats, 45 MB)')))
  return box
}
// the course line on or off (LINE, and the key L on every page), this browser's choice, on unless turned off
const loadLine = () => { try { return localStorage.getItem('ghostline-line') !== '0' } catch { return true } }
const saveLine = (on: boolean) => { try { localStorage.setItem('ghostline-line', on ? '1' : '0') } catch { /* private window: not kept */ } }
let lineRedraw = () => {}
export const lineButton = (stage: Stage, redraw: () => void) => {
  lineRedraw = redraw
  return Object.assign(document.createElement('button'), { textContent: 'LINE', title: L('コースの線（L）', 'Course line (L)'), className: (stage.showLine ? 'on ' : '') + 'opt',
    onclick: () => { stage.setShowLine(!stage.showLine); redraw() } })
}
// on a phone the header keeps the cameras and puts the rest (each marked .opt: the scan's quality, LINE, the language,
// Compare, ...) behind ⋯, shown under the cameras while it is open (look.css); on a wide screen ⋯ is not shown
export const moreButton = () => {
  const open = () => !!document.querySelector('.bar.top.open')
  const b = Object.assign(document.createElement('button'), { textContent: '⋯', title: L('ほかの設定', 'More'), className: 'more' + (open() ? ' on' : '') })
  b.onclick = () => { b.classList.toggle('on', !!b.closest('.bar.top')?.classList.toggle('open')) }
  return b
}
export function createStage(canvas: HTMLCanvasElement, base: string): Stage {
  const app = new pc.Application(canvas, { mouse: new pc.Mouse(canvas), touch: new pc.TouchDevice(canvas), graphicsDeviceOptions: { antialias: !PHONE } })   // MSAA: the trail fades by alpha-to-coverage, as mgp.ts - not on a phone, where 4x the splats' fill dropped the frame rate
  // the canvas is as large as the page's CSS makes it (the whole window, or the part over the live page's progress band)
  app.setCanvasFillMode(pc.FILLMODE_NONE); app.setCanvasResolution(pc.RESOLUTION_AUTO)
  // (resizeCanvas writes the size into the canvas's style: cleared again, or the first size would stick over the CSS)
  new ResizeObserver(() => { app.resizeCanvas(canvas.clientWidth, canvas.clientHeight); canvas.style.width = canvas.style.height = '' }).observe(canvas)
  // the bottom bars' heights as --foot and --time (look.css stacks the timeline and the pilots over them; the footer wraps on a phone)
  const bars = new ResizeObserver(() => { for (const [v, q] of [['--head', '.bar.top'], ['--foot', '.bar.foot'], ['--time', '.bar.time']]) document.documentElement.style.setProperty(v, `${document.querySelector(q)?.getBoundingClientRect().height ?? 0}px`) })
  for (const q of ['.bar.top', '.bar.foot', '.bar.time']) { const e = document.querySelector(q); if (e) bars.observe(e) }
  const cam = new pc.Entity('cam'); cam.addComponent('camera', { clearColor: hex(css('--paper') || '#f3f4f6'), fov: 60, nearClip: 0.1, farClip: 600 }); app.root.addChild(cam)

  // --- camera (as livepage.ts): orbit turns slowly round the course's middle, as near as holds the course and everyone up;
  // top looks straight down; follow sits behind and above one pilot (an up / down drag raises / lowers it). Drag turns and tilts, the wheel zooms (Free: freecam.ts); a switch glides.
  const view = { yawOffset: 30, topYaw: 0, pitch: innerHeight > innerWidth ? PITCH_TALL : PITCH0, zoom: 1, followUp: 3 }
  const home = { target: new pc.Vec3(), dist: 70 }, orb = { target: new pc.Vec3(), dist: 70, init: false }
  let orbitMin = 12                                   // the orbit's nearest (m) while it holds pilots or the course (index.json "orbitMin")
  const blendFrom = { pos: new pc.Vec3(), rot: new pc.Quat() }; let blendT = BLEND
  let course: pc.Vec3[] = [], line: pc.Vec3[] = [], lineS: number[] = [], low = -Infinity   // low: the course line's lowest point (the orbit stays over it)
  // Free (aud/freecam.ts): supersplat-viewer's fly mode. The left drag looks, the right drag pans, the wheel moves forward,
  // WASD / QE fly (shift faster, ctrl slower); one finger looks, two pan and spread to move forward. The room's walls go
  // as in the orbit: from outside, those in front of the inside; with the camera in the room, none
  const free = new FreeCam()
  let drag: { x: number; y: number; pan: boolean; touch: boolean } | null = null
  // two fingers pinch the zoom (as the wheel; Free pans and flies) and do not turn; lifting one does not start a drag
  const touches = new Map<number, { x: number; y: number }>(); let pinch: { d: number; zoom: number; mx: number; my: number } | null = null
  const spread = () => { const [a, b] = [...touches.values()]; return Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)) }
  const setZoom = (z: number) => { view.zoom = Math.max(0.4, Math.min(3, z)) }
  const mid = () => { const [a, b] = [...touches.values()]; return { mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 } }
  const rebase = () => { pinch = touches.size === 2 ? { d: spread(), zoom: view.zoom, ...mid() } : null }   // whenever two fingers are down, measured from now
  canvas.addEventListener('pointerdown', e => {
    touches.set(e.pointerId, { x: e.clientX, y: e.clientY }); canvas.setPointerCapture(e.pointerId)
    rebase(); drag = touches.size === 1 ? { x: e.clientX, y: e.clientY, pan: e.button !== 0, touch: e.pointerType === 'touch' } : null
  })
  const lift = (e: PointerEvent) => { if (!touches.delete(e.pointerId)) return; rebase(); drag = null }
  for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture'] as const) canvas.addEventListener(ev, lift)
  canvas.addEventListener('contextmenu', e => e.preventDefault())   // the right drag pans in Free
  canvas.addEventListener('pointermove', e => {
    if (touches.has(e.pointerId)) touches.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (pinch) {
      if (stage.mode !== 'free') { setZoom(pinch.zoom * pinch.d / spread()); return }
      const m = mid(), d = spread(); free.pinch(d - pinch.d); free.pan(m.mx - pinch.mx, m.my - pinch.my); pinch.d = d; pinch.mx = m.mx; pinch.my = m.my; return
    }
    if (!drag) return
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY
    if (stage.mode === 'free') { if (drag.pan) free.pan(dx, dy); else free.look(dx, dy, drag.touch) }
    else if (stage.mode === 'top') view.topYaw -= dx * 0.3
    else if (stage.mode[0] === 'p') view.followUp = Math.max(0.5, Math.min(15, view.followUp + dy * 0.03))
    else { view.yawOffset -= dx * 0.3; view.pitch = Math.max(-85, Math.min(15, view.pitch - dy * 0.3)) }
  })
  canvas.addEventListener('wheel', e => { if (stage.mode === 'free') free.wheel(e.deltaY); else setZoom(view.zoom * Math.exp(e.deltaY * 0.001)); e.preventDefault() }, { passive: false })
  window.addEventListener('keydown', e => { if (stage.mode === 'free' && !['INPUT', 'SELECT', 'TEXTAREA'].includes((e.target as HTMLElement).tagName) && free.key(e, true)) e.preventDefault() })
  window.addEventListener('keyup', e => free.key(e, false)); window.addEventListener('blur', () => free.release())
  window.addEventListener('keydown', e => {
    if ((e.key === 'l' || e.key === 'L') && !e.metaKey && !e.ctrlKey && !['INPUT', 'SELECT', 'TEXTAREA'].includes((e.target as HTMLElement).tagName)) { stage.setShowLine(!stage.showLine); lineRedraw() }
  })

  const pilots = new Map<string, Pilot>()
  function cameraAt(t: number, dt: number) {
    const up = [...pilots.values()].filter(p => p.pos && p.state === 'flying').map(p => p.pos as pc.Vec3)
    if (!orb.init) { orb.target.copy(home.target); orb.dist = home.dist; orb.init = true }
    if (stage.mode === 'free') { free.apply(cam, dt, canvas.clientWidth, canvas.clientHeight); return }
    if (stage.mode === 'top') {
      cam.setPosition(home.target.x, home.target.y + home.dist * 1.1 * view.zoom, home.target.z); cam.setEulerAngles(-89.9, view.topYaw, 0); return
    }
    if (stage.mode[0] === 'p') {
      const p = pilots.get(stage.mode.slice(1))
      if (p?.pos) {
        const now = p.pos.clone(), fwd = p.fwd
        cam.setPosition(now.clone().sub(fwd.clone().mulScalar(8 * view.zoom)).add(new pc.Vec3(0, view.followUp * view.zoom, 0))); cam.lookAt(now.add(fwd.clone().mulScalar(5))); return
      }
    }
    const r = new pc.Quat().setFromEulerAngles(view.pitch, view.yawOffset + TURN * t, 0)
    const dir = r.transformVector(new pc.Vec3(0, 0, 1), new pc.Vec3()), right = r.transformVector(new pc.Vec3(1, 0, 0), new pc.Vec3()), upv = r.transformVector(new pc.Vec3(0, 1, 0), new pc.Vec3())
    const goal = course.length ? home.target : up.length ? up.reduce((a, b) => a.clone().add(b), new pc.Vec3()).mulScalar(1 / up.length) : home.target
    orb.target.lerp(orb.target, goal, 1 - Math.exp(-dt / 1.5))
    const tanV = Math.tan(30 * Math.PI / 180) * 0.85, tanH = tanV * canvas.clientWidth / Math.max(1, canvas.clientHeight)
    let need = course.length || up.length ? orbitMin : home.dist
    for (const q of course.length ? course.concat(up) : up) { const v = q.clone().sub(orb.target), a = v.dot(right), b = v.dot(upv), c = v.dot(dir); need = Math.max(need, Math.abs(a) / tanH + c, Math.abs(b) / tanV + c) }
    need *= view.zoom; orb.dist += (need - orb.dist) * (1 - Math.exp(-dt / (need > orb.dist ? 1.0 : 2.5)))
    // dragged up to look slightly upwards (15 deg), the orbit stays 0.3 m over the course's lowest point, so it does not go through the floor
    const eyeAt = r.transformVector(new pc.Vec3(0, 0, orb.dist), new pc.Vec3()).add(orb.target); eyeAt.y = Math.max(eyeAt.y, low + 0.3)
    cam.setPosition(eyeAt); cam.lookAt(orb.target)
  }

  // --- the trail (as livepage.ts): an opaque, depth-writing band facing the camera, before the splats, thinning to its old
  // end by alpha-to-coverage (as mgp.ts; a phone, without MSAA, by a blue-noise dither moved every frame); broken where a frame has no position
  const trailMat = new pc.StandardMaterial()
  { const m = trailMat; m.useLighting = false; m.diffuse = new pc.Color(0, 0, 0); m.cull = pc.CULLFACE_NONE; m.emissiveVertexColor = true; m.emissive = new pc.Color(1, 1, 1)
    m.blendType = pc.BLEND_NONE; m.depthWrite = true; m.depthTest = true; m.opacityVertexColor = true; m.opacityVertexColorChannel = 'a'; if (PHONE) m.opacityDither = pc.DITHER_BLUENOISE; else m.alphaToCoverage = true; m.update() }
  // and again after the splats, in another colour, where splats hide it (under an upper floor, behind a wall): every splat
  // fragment that passes the depth test - in front of whatever was drawn before the splats - counts up the stencil, and the
  // x-ray passes draw only where XRAY_SPLATS or more lie in front (a floor's thick layer of splats, not a thin haze), so
  // not over the parts that show (?xs=<n> tries another count)
  const XRAY_SPLATS = Math.max(1, Math.min(255, Number(SHOT.get('xs')) || 12))
  const STENCIL_MARK = new pc.StencilParameters({ func: pc.FUNC_ALWAYS, ref: 0, readMask: 255, writeMask: 255, fail: pc.STENCILOP_KEEP, zfail: pc.STENCILOP_KEEP, zpass: pc.STENCILOP_INCREMENT })
  const STENCIL_HIDDEN = new pc.StencilParameters({ func: pc.FUNC_LESSEQUAL, ref: XRAY_SPLATS, readMask: 255, writeMask: 0, fail: pc.STENCILOP_KEEP, zfail: pc.STENCILOP_KEEP, zpass: pc.STENCILOP_KEEP })
  app.systems.gsplat!.on('material:created', (m: pc.Material) => { m.stencilFront = m.stencilBack = STENCIL_MARK; m.update() })
  const cutaway = Cutaway.fromQuery(app, SHOT)   // on by default (?cutaway=0 off), ?xray= (aud/cutaway.ts)
  const xray = (o: number, tint: pc.Color) => { const m = new pc.StandardMaterial(); m.useLighting = false; m.diffuse = new pc.Color(0, 0, 0); m.cull = pc.CULLFACE_NONE
    m.emissiveVertexColor = true; m.emissive = tint; m.opacityVertexColor = true; m.opacityVertexColorChannel = 'a'; m.opacity = o
    m.blendType = pc.BLEND_NORMAL; m.depthWrite = false; m.depthTest = false; m.stencilFront = m.stencilBack = STENCIL_HIDDEN; m.update(); return m }
  const trailXray = xray(TRAIL_XRAY, new pc.Color(0.55, 0.55, 0.55))   // the pilot's colour, darker
  function drawTrail(mesh: pc.Mesh, pts: (number[] | null)[], c: pc.Color, eye: pc.Vec3, pxWorld: number, full = false) {
    const pos: number[] = [], col: number[] = [], nrm: number[] = [], idx: number[] = [], n = pts.length
    const tan = new pc.Vec3(), vw = new pc.Vec3(), side = new pc.Vec3(), a = new pc.Vec3(), b = new pc.Vec3(), q = new pc.Vec3(); let run = 0
    for (let k = 0; k < n; k++) {
      const p = pts[k]; if (!p) { run = 0; continue }
      const pa = pts[k - 1] ?? p, pb = pts[k + 1] ?? p
      q.set(p[0], p[1], p[2]); a.set(pa[0], pa[1], pa[2]); b.set(pb[0], pb[1], pb[2])
      tan.sub2(a, b); if (tan.lengthSq() < 1e-12) tan.set(1, 0, 0); tan.normalize(); vw.sub2(eye, q)
      const L = vw.length(), al = full ? 0.25 : k / Math.max(1, n - 1), w = (1 + (TRAIL_PX - 1) * al) / 2 * pxWorld * L
      side.cross(tan, vw.normalize()); if (side.lengthSq() < 1e-12) side.set(0, 1, 0); side.normalize().mulScalar(w); q.add(vw.mulScalar(pullOf(L)))
      pos.push(q.x + side.x, q.y + side.y, q.z + side.z, q.x - side.x, q.y - side.y, q.z - side.z); col.push(c.r, c.g, c.b, al, c.r, c.g, c.b, al); nrm.push(0, 1, 0, 0, 1, 0)
      if (run) { const i = pos.length / 3 - 2; idx.push(i - 2, i - 1, i, i - 1, i + 1, i) }
      run++
    }
    if (idx.length < 3) { emptyMesh(mesh); return }
    mesh.setPositions(pos); mesh.setNormals(nrm); mesh.setColors(col); mesh.setIndices(idx); mesh.update(pc.PRIMITIVE_TRIANGLES)
  }

  // --- the course line: a white dashed band, written with depth before the splats as the trail (the splats in front of it
  // cover it), and an x-ray of it after them, so a stretch under an upper floor or behind a wall shows faintly
  const lineMesh = new pc.Mesh(app.graphicsDevice); lineMesh.clear(true, false); emptyMesh(lineMesh); const lineEnts: pc.Entity[] = []
  { const m = new pc.StandardMaterial(); m.useLighting = false; m.diffuse = new pc.Color(0, 0, 0); m.cull = pc.CULLFACE_NONE; m.emissiveVertexColor = true; m.emissive = new pc.Color(1, 1, 1)
    m.opacityVertexColor = true; m.opacityVertexColorChannel = 'a'; m.opacity = LINE_A; m.blendType = pc.BLEND_NONE; m.depthWrite = true; m.depthTest = true
    if (PHONE) m.opacityDither = pc.DITHER_BLUENOISE; else m.alphaToCoverage = true; m.update()
    const e = new pc.Entity('course'); e.addComponent('render', { meshInstances: [new pc.MeshInstance(lineMesh, m)] }); app.root.addChild(e)
    const x = new pc.Entity('course-xray'); x.addComponent('render', { meshInstances: [new pc.MeshInstance(lineMesh, xray(LINE_XRAY, new pc.Color(0.45, 0.85, 1)))], layers: [pc.LAYERID_IMMEDIATE] }); app.root.addChild(x)
    lineEnts.push(e, x); for (const q of lineEnts) q.enabled = loadLine() }
  function drawLine(eye: pc.Vec3) {
    const n = line.length; if (!n) { emptyMesh(lineMesh); return }
    const pos: number[] = [], col: number[] = [], nrm: number[] = [], idx: number[] = [], tan = new pc.Vec3(), vw = new pc.Vec3(), side = new pc.Vec3()
    for (let k = 0; k < n; k++) {
      tan.sub2(line[(k - 1 + n) % n], line[(k + 1) % n]).normalize(); vw.sub2(eye, line[k]); const L = vw.length()
      side.cross(tan, vw.normalize()).normalize().mulScalar(LINE_W / 2); const q = vw.mulScalar(pullOf(L)).add(line[k])
      const al = lineS[k] % (LINE_DASH + LINE_GAP) < LINE_DASH ? 1 : 0
      pos.push(q.x + side.x, q.y + side.y, q.z + side.z, q.x - side.x, q.y - side.y, q.z - side.z); col.push(1, 1, 1, al, 1, 1, 1, al); nrm.push(0, 1, 0, 0, 1, 0)
      const i = 2 * k, j = 2 * ((k + 1) % n); idx.push(i, i + 1, j, i + 1, j + 1, j)
    }
    lineMesh.setPositions(pos); lineMesh.setNormals(nrm); lineMesh.setColors(col); lineMesh.setIndices(idx); lineMesh.update(pc.PRIMITIVE_TRIANGLES)
  }

  // --- a pilot: sphere, trail and tag; drawn each frame from what the page last showed
  class Pilot implements StagePilot {
    line = false; color: string; col: pc.Color; pos: pc.Vec3 | null = null; state: PilotState = 'nopic'; fwd = new pc.Vec3(0, 0, -1); prev: pc.Vec3 | null = null
    mat = new pc.StandardMaterial(); dot = new pc.Entity('dot'); trail: pc.Mesh; trailE = new pc.Entity('trail'); trailX = new pc.Entity('trail-xray'); dotX = new pc.Entity('dot-xray'); matX = new pc.StandardMaterial(); pts: (number[] | null)[] = []; tag = document.createElement('div'); label = ''; sx = 0; sy = 0; order = 0; lift = 0
    constructor(public stream: string, public slot: Slot) {
      this.color = css('--' + slot) || '#5b4bff'; this.col = hex(this.color)
      const m = this.mat; m.useLighting = false; m.diffuse = new pc.Color(0, 0, 0); m.emissive = this.col; m.update()
      this.dot.addComponent('render', { type: 'sphere', material: m, castShadows: false }); this.dot.setLocalScale(0.14, 0.14, 0.14); this.dot.enabled = false; app.root.addChild(this.dot)
      this.trail = new pc.Mesh(app.graphicsDevice); this.trail.clear(true, false); emptyMesh(this.trail)
      this.trailE.addComponent('render', { meshInstances: [new pc.MeshInstance(this.trail, trailMat)] }); app.root.addChild(this.trailE)
      this.trailX.addComponent('render', { meshInstances: [new pc.MeshInstance(this.trail, trailXray)], layers: [pc.LAYERID_IMMEDIATE] }); app.root.addChild(this.trailX)
      const mx = this.matX; mx.useLighting = false; mx.diffuse = new pc.Color(0, 0, 0); mx.emissive = this.col; mx.opacity = 0.35; mx.blendType = pc.BLEND_NORMAL; mx.depthWrite = false; mx.depthTest = false; mx.stencilFront = mx.stencilBack = STENCIL_HIDDEN; mx.update()
      this.dotX.addComponent('render', { type: 'sphere', material: mx, castShadows: false, layers: [pc.LAYERID_IMMEDIATE] }); this.dotX.setLocalScale(0.14, 0.14, 0.14); this.dotX.enabled = false; app.root.addChild(this.dotX)
      this.tag.className = 'tag'; this.tag.style.setProperty('--c', this.color); this.tag.hidden = true; document.body.appendChild(this.tag)
      this.tag.style.pointerEvents = 'auto'; this.tag.style.cursor = 'pointer'; this.tag.onclick = () => stage.toggleFollow(this.stream)
    }
    show(pos: number[], trail: (number[] | null)[], state: PilotState, label: { name: string; flag: string; note: string }) {
      const now = new pc.Vec3(pos[0], pos[1], pos[2]), flat = new pc.Vec3(pos[0], 0, pos[2])
      if (this.prev) { const v = flat.clone().sub(this.prev); if (v.length() > 0.02) this.fwd.lerp(this.fwd, v.normalize(), 0.08).normalize() }
      this.prev = flat; this.pos = now; this.state = state; this.pts = trail
      this.dot.enabled = this.dotX.enabled = true; this.dot.setPosition(now); this.dotX.setPosition(now)
      const key = [state, label.flag, label.name, label.note].join('|')
      if (key === this.label) return; this.label = key                 // the material and the tag only when they change
      this.mat.emissive = this.matX.emissive = state === 'flying' ? this.col : cLost; this.mat.update(); this.matX.update()
      const b = document.createElement('b'); b.textContent = label.name ? '' : this.stream   // the audience knows pilots by name: the cell only when there is none
      const who = Object.assign(document.createElement('span'), { textContent: (label.flag + ' ' + label.name).trim() })
      const note = Object.assign(document.createElement('small'), { textContent: label.note })
      this.tag.classList.toggle('lost', state !== 'flying'); this.tag.replaceChildren(b, ...(label.name ? [who] : []), ...(label.note ? [note] : []))
    }
    destroy() { this.dot.destroy(); this.dotX.destroy(); this.trailE.destroy(); this.trailX.destroy(); this.tag.remove() }
    hide() { this.pos = null; this.state = 'nopic'; this.pts = []; this.prev = null; this.dot.enabled = this.dotX.enabled = false; this.tag.hidden = true; emptyMesh(this.trail) }
    draw(eye: pc.Vec3, pxWorld: number): boolean {               // true: its tag is on screen, at sx, sy
      if (!this.pos) return false
      drawTrail(this.trail, this.pts, this.state === 'flying' ? this.col : cLost, eye, pxWorld, this.line)
      const v = eye.clone().sub(this.pos), L = v.length(), pulled = v.normalize().mulScalar(pullOf(L)).add(this.pos); this.dot.setPosition(pulled); this.dotX.setPosition(pulled)
      if (this.line) { this.dot.enabled = this.dotX.enabled = false; this.tag.hidden = true; return false }
      const s = cam.camera!.worldToScreen(this.pos, new pc.Vec3()), behind = cam.forward.dot(this.pos.clone().sub(eye)) < 0
      this.sx = s.x + at.left; this.sy = s.y + at.top                   // the canvas's own place on the page (below the header)
      this.tag.hidden = behind; this.tag.style.left = this.sx + 'px'; return !behind
    }
  }

  // --- an indoor venue (index.json "room" and "cut", the page frame): the room's near walls and roof between the camera and
  // the room are not drawn (occlude.ts, as the live page's), and an upper floor hides the floor below it from above, so
  // splats over the cut fade out. The cut is the lowest of "cut" over everyone flying (the followed pilot alone in FOLLOW),
  // none when someone is higher, and glides there once that has held CUT_HOLD s (no flicker as a pilot crosses)
  const CUT_ROOM = 0.3, CUT_HOLD = 0.5, CUT_GLIDE = 0.35, CUT_NONE = 40
  const cut = { levels: [] as number[], y: NaN, goal: NaN, held: 0 }
  let room: Room | null = null, occ: { e: pc.Entity; o: RoomOcclusion } | null = null
  // ?room: the room's shape on screen - the splats it hides painted red instead (occlude.ts OCC_DEBUG), the floor plan as
  // given (white) and pulled in by the margin (yellow, the prism the test uses) from its floor up to its top (the roof
  // planes', sampled every metre along each wall)
  const ROOM_DEBUG = SHOT.has('room')
  function drawRoom(r: Room, o: RoomOcclusion) {
    if (!r.polygon) return
    const m = r.margin ?? 0.25
    const plan = (P: [number, number][], c: pc.Color, y0: number, up: number) => P.forEach(([x, z], i) => {
      const [nx, nz] = P[(i + 1) % P.length], k = Math.max(1, Math.ceil(Math.hypot(nx - x, nz - z)))
      app.drawLine(new pc.Vec3(x, y0, z), new pc.Vec3(nx, y0, nz), c, false)
      app.drawLine(new pc.Vec3(x, y0, z), new pc.Vec3(x, o.top(x, z) + up, z), c, false)
      for (let j = 0; j < k; j++) {
        const ax = x + (nx - x) * j / k, az = z + (nz - z) * j / k, bx = x + (nx - x) * (j + 1) / k, bz = z + (nz - z) * (j + 1) / k
        app.drawLine(new pc.Vec3(ax, o.top(ax, az) + up, az), new pc.Vec3(bx, o.top(bx, bz) + up, bz), c, false)
      }
    })
    plan(r.polygon, pc.Color.WHITE, r.y0, m)
    plan(insetPolygon(r.polygon, m), pc.Color.YELLOW, r.y0 + m, 0)
  }
  function roomAt(dt: number) {
    const g = shown?.e.gsplat
    if (!room || !g || !shown) return
    if (occ?.e !== shown.e) { occ = { e: shown.e, o: new RoomOcclusion(g, room, ROOM_DEBUG) }; cut.y = NaN }
    occ.o.update(cam.getPosition())
    if (ROOM_DEBUG) drawRoom(room, occ.o)
    if (!cut.levels.length) return
    const follow = stage.mode[0] === 'p' ? pilots.get(stage.mode.slice(1)) : undefined
    const ys = (follow ? [follow] : [...pilots.values()]).filter(p => p.pos && p.state === 'flying').map(p => (p.pos as pc.Vec3).y)
    const high = ys.length ? Math.max(...ys) + CUT_ROOM : Infinity
    const goal = cut.levels.find(l => l > high) ?? CUT_NONE
    if (goal !== cut.goal) { cut.goal = goal; cut.held = 0 } else cut.held += dt
    if (Number.isNaN(cut.y)) cut.y = goal
    else if (cut.held >= CUT_HOLD) cut.y += (goal - cut.y) * (1 - Math.exp(-dt / CUT_GLIDE))
    occ.o.setCut(cut.y >= CUT_NONE - 0.5 ? Infinity : cut.y)
  }

  let t = 0, at = canvas.getBoundingClientRect()
  app.on('update', (dt: number) => {
    if (!SHOT.has('still')) t += dt   // ?still: the orbit stays at its first angle (screenshots from one view)
    cameraAt(t, dt); roomAt(dt)
    cutaway?.update(cam.getPosition(), [...pilots.values()].filter(p => p.pos && p.state === 'flying').map(p => p.pos as pc.Vec3))
    if (blendT < BLEND) { blendT = Math.min(BLEND, blendT + dt); const u = blendT / BLEND, k = u * u * (3 - 2 * u)
      cam.setPosition(new pc.Vec3().lerp(blendFrom.pos, cam.getPosition(), k)); cam.setRotation(new pc.Quat().slerp(blendFrom.rot, cam.getRotation(), k)) }
    const eye = cam.getPosition(), pxWorld = 2 * Math.tan(30 * Math.PI / 180) / Math.max(1, canvas.clientHeight)
    drawLine(eye); if (PHONE) trailMat.setParameter('blueNoiseJitter', [Math.random(), Math.random(), Math.random(), Math.random()])
    // name tags that would overlap are stacked upwards, as the venue's 3D page's (livepage.ts): the lowest on screen stays
    // over its sphere, each other one goes up past the tags it would cover and glides there; two within 30 px of each
    // other keep last frame's order, so pilots at about the same height do not trade places every few frames
    at = canvas.getBoundingClientRect()
    const on = [...pilots.values()].filter(p => p.draw(eye, pxWorld))
    on.sort((a, b) => Math.abs(a.sy - b.sy) < 30 ? a.order - b.order : b.sy - a.sy); on.forEach((p, i) => { p.order = i })
    const boxes: number[][] = []
    for (const p of on) {
      const w = p.tag.offsetWidth, h = p.tag.offsetHeight + 2, x0 = p.sx - w / 2, x1 = p.sx + w / 2
      let y = p.sy
      for (let moved = true; moved;) { moved = false; for (const r of boxes) if (x0 < r[1] && x1 > r[0] && y > r[2] - h && y < r[2] + h) { y = r[2] - h; moved = true } }
      boxes.push([x0, x1, y]); p.lift += (p.sy - y - p.lift) * (1 - Math.exp(-dt / 0.15))
      p.tag.style.top = p.sy - p.lift + 'px'
    }
  })
  app.start()

  // the scan shown: one entity, replaced once the next one is in (the old one stays on screen meanwhile)
  let ix: any = null, prog: ((f: number) => void) | undefined, shown: { name: string; e: pc.Entity } | null = null, want = ''
  const scanName = () => pickScan(ix, stage.hq)
  async function showScan(name: string) {
    if (shown?.name === name) return; want = name
    const url = URL.createObjectURL(await sceneBlob(`${base}scene/${name}.sog`, prog))
    await new Promise<void>((ok, bad) => {
      const a = new pc.Asset(name, 'gsplat', { url, filename: `${name}.sog` }); app.assets.add(a)
      a.on('load', () => {
        URL.revokeObjectURL(url)
        if (want !== name) { app.assets.remove(a); a.unload(); ok(); return }      // switched again while this one came
        const e = new pc.Entity('scene'); e.addComponent('gsplat', { asset: a }); app.root.addChild(e)
        if (shown) { const old = shown.e.gsplat?.asset; shown.e.destroy(); const oa = typeof old === 'number' ? app.assets.get(old) : old; if (oa) { app.assets.remove(oa); oa.unload() } }
        shown = { name, e }; ok()
      }); a.on('error', bad); app.assets.load(a)
    })
  }
  // the day's sky (index.json "sky", tools/x6/jdl-r7-sky.sh): a scan trained with the sky masked out ends in the clear colour
  // above the trees, so the photographs' own sky goes round it as a skybox, baked into a cubemap once (sky.ts, as the viewer)
  const loadSky = (url: string) => {
    const img = new Image(); img.crossOrigin = 'anonymous'
    img.onload = () => { app.scene.skyboxMip = 0; app.scene.skybox = bakeSky(app.graphicsDevice, img) }
    img.onerror = () => console.error('sky failed:', url); img.src = url
  }
  const json = (u: string) => fetch(u).then(r => { if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json() })
  const stage: Stage = {
    app, mode: 'orbit', onMode: () => {},
    // the scan read here, so its progress can be shown (f: 0-1, or NaN when the size is not known), then handed to PlayCanvas
    async loadScene(progress) {
      ix = await json(base + 'index.json'); prog = progress
      // the orbit's middle: the course line's when it is in (whichever of the two files arrives last), else index.json's
      if (ix.orbit?.target && !course.length) { home.target.set(ix.orbit.target[0], ix.orbit.target[1], ix.orbit.target[2]); home.dist = ix.orbit.dist ?? home.dist; orb.init = false }
      // an indoor venue (UDGP 2026 R3) looks down over its walls: the default pitch sees only the nearest wall from outside
      if (typeof ix.orbitPitch === 'number') view.pitch = ix.orbitPitch
      // ?yaw= / ?pitch= (degrees): the orbit's angle, for screenshots from chosen views (with ?still)
      if (SHOT.has('yaw')) view.yawOffset = Number(SHOT.get('yaw'))
      if (SHOT.has('pitch')) view.pitch = Number(SHOT.get('pitch'))
      if (ix.room) room = ix.room as Room
      // indoors the orbit stays far enough to be over the roof (outside the room, so its near walls and roof go)
      if (typeof ix.orbitMin === 'number') orbitMin = ix.orbitMin
      if (Array.isArray(ix.cut)) cut.levels = ix.cut.filter((v: unknown) => typeof v === 'number').sort((a: number, b: number) => a - b)
      if (ix.sky && !SHOT.get('nosky')) loadSky(base + ix.sky)   // beside the scan, not waited for
      await showScan(scanName())
      return { title: ix.title }
    },
    // HQ: the full scan (3M splats, 45 MB) instead of the lite one (0.8M, 12 MB) every page starts with; this browser remembers
    hq: loadHQ(),
    async setHQ(on) { stage.hq = on; saveHQ(on); if (ix) await showScan(scanName()) },
    setCourse(track) {                                       // the course line; it may come later or be replaced during the day
      if (!track?.length) { course = []; line = []; lineS = []; low = -Infinity; return }
      course = track.filter((_, k) => k % 8 === 0).map(q => new pc.Vec3(q[0], q[1], q[2]))
      line = track.map(q => new pc.Vec3(q[0], q[1], q[2])); lineS = [0]; for (let k = 1; k < line.length; k++) lineS.push(lineS[k - 1] + line[k].distance(line[k - 1]))
      const mid = (k: number) => { const v = track.map(q => q[k]); return (Math.min(...v) + Math.max(...v)) / 2 }, ys = track.map(q => q[1]).sort((a, b) => a - b)
      home.target.set(mid(0), ys[ys.length >> 1], mid(2)); low = ys[0]; orb.init = false
    },
    pilot(stream, slot) { let p = pilots.get(stream); if (!p || p.slot !== slot) { p?.destroy(); p = new Pilot(stream, slot); pilots.set(stream, p) } return p },
    toggleFollow(key) { stage.setMode(stage.mode === `p${key}` ? 'orbit' : `p${key}`) },
    setMode(m) { if (m === stage.mode) return; blendFrom.pos.copy(cam.getPosition()); blendFrom.rot.copy(cam.getRotation()); blendT = 0
      if (m === 'free') free.enter(cam)   // from where the camera is
      stage.mode = m; stage.onMode(m) },
    clear() { for (const p of pilots.values()) p.hide() },
    showLine: loadLine(),
    setShowLine(on) { stage.showLine = on; for (const q of lineEnts) q.enabled = on; saveLine(on) },
  }
  Object.assign(window, { ghostline: { app, pc, pilots, stage, free } })   // for checking the page from outside
  return stage
}
