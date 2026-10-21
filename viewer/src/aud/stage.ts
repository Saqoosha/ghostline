// The audience pages' 3D (watch.ts live, heat.ts replay): the scan, the course line, a sphere, a trail and a name tag per
// pilot, and the camera - Orbit, Top, Follow one pilot. Drawn as livepage.ts draws them (that page is the venue's; this one
// leaves out its Free camera, BA switch and sliders). A pilot's colour is their cell's place in the grid (--tl ... --br, look.css).
import * as pc from 'playcanvas'

export type Slot = 'tl' | 'tr' | 'bl' | 'br'
export const SLOTS: Slot[] = ['tl', 'tr', 'bl', 'br']
export type Mode = 'orbit' | 'top' | `p${string}`
export type PilotState = 'flying' | 'lost' | 'nopic'
export interface StagePilot { stream: string; slot: Slot; color: string; pos: pc.Vec3 | null
  show(pos: number[], trail: (number[] | null)[], state: PilotState, label: { name: string; flag: string; note: string }): void; hide(): void }
export interface Stage { app: pc.Application; mode: Mode; onMode: (m: Mode) => void
  loadScene(): Promise<{ title?: string }>; setCourse(track: number[][] | null): void; pilot(stream: string, slot: Slot): StagePilot
  setMode(m: Mode): void; clear(): void }

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()
const hex = (h: string) => new pc.Color(parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255)
const TRAIL_PX = 5, LINE_W = 0.12, LINE_DASH = 1.0, LINE_GAP = 0.7, LINE_A = 0.22, PITCH0 = -18, TURN = 6, BLEND = 0.7
const cLost = new pc.Color(0.55, 0.57, 0.6)
// a phone gets the light scan (index.json "lite", tools/publish-event.sh) unless ?q=full; a tall screen looks down more steeply,
// or the orbit, backing off to hold the course across the narrow width, ends up inside the trees round it
const PHONE = new URLSearchParams(location.search).get('q') !== 'full' && matchMedia('(pointer: coarse) and (max-width: 1000px) and (max-height: 1000px)').matches   // not a tablet
const PITCH_TALL = -38

function emptyMesh(m: pc.Mesh) { m.setPositions([0, 0, 0, 0, 0, 0, 0, 0, 0]); m.setNormals([0, 1, 0, 0, 1, 0, 0, 1, 0]); m.setColors(new Array(12).fill(0)); m.setIndices([0, 1, 2]); m.update(pc.PRIMITIVE_TRIANGLES) }

export function createStage(canvas: HTMLCanvasElement, base: string): Stage {
  const app = new pc.Application(canvas, { mouse: new pc.Mouse(canvas), touch: new pc.TouchDevice(canvas), graphicsDeviceOptions: { antialias: false } })
  app.setCanvasFillMode(pc.FILLMODE_FILL_WINDOW); app.setCanvasResolution(pc.RESOLUTION_AUTO)
  window.addEventListener('resize', () => app.resizeCanvas())
  // the bottom bars' heights as --foot and --time (look.css stacks the timeline and the pilots over them; the footer wraps on a phone)
  const bars = new ResizeObserver(() => { for (const [v, q] of [['--foot', '.bar.foot'], ['--time', '.bar.time']]) document.documentElement.style.setProperty(v, `${document.querySelector(q)?.getBoundingClientRect().height ?? 0}px`) })
  for (const q of ['.bar.foot', '.bar.time']) { const e = document.querySelector(q); if (e) bars.observe(e) }
  const cam = new pc.Entity('cam'); cam.addComponent('camera', { clearColor: hex(css('--paper') || '#f3f4f6'), fov: 60, nearClip: 0.1, farClip: 600 }); app.root.addChild(cam)

  // --- camera (as livepage.ts): orbit turns slowly round the course's middle, as near as holds the course and everyone up;
  // top looks straight down; follow sits behind and above one pilot. Drag turns and tilts, the wheel zooms; a switch glides.
  const view = { yawOffset: 30, topYaw: 0, pitch: innerHeight > innerWidth ? PITCH_TALL : PITCH0, zoom: 1 }
  const home = { target: new pc.Vec3(), dist: 70 }, orb = { target: new pc.Vec3(), dist: 70, init: false }
  const blendFrom = { pos: new pc.Vec3(), rot: new pc.Quat() }; let blendT = BLEND
  let course: pc.Vec3[] = [], line: pc.Vec3[] = [], lineS: number[] = []
  let drag: { x: number; y: number } | null = null
  canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId) })
  canvas.addEventListener('pointerup', () => { drag = null })
  canvas.addEventListener('pointermove', e => {
    if (!drag) return
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY
    if (stage.mode === 'top') view.topYaw -= dx * 0.3
    else { view.yawOffset -= dx * 0.3; view.pitch = Math.max(-85, Math.min(-5, view.pitch - dy * 0.3)) }
  })
  canvas.addEventListener('wheel', e => { view.zoom = Math.max(0.4, Math.min(3, view.zoom * Math.exp(e.deltaY * 0.001))); e.preventDefault() }, { passive: false })

  const pilots = new Map<string, Pilot>()
  function cameraAt(t: number, dt: number) {
    const up = [...pilots.values()].filter(p => p.pos && p.state === 'flying').map(p => p.pos as pc.Vec3)
    if (!orb.init) { orb.target.copy(home.target); orb.dist = home.dist; orb.init = true }
    if (stage.mode === 'top') {
      cam.setPosition(home.target.x, home.target.y + home.dist * 1.1 * view.zoom, home.target.z); cam.setEulerAngles(-89.9, view.topYaw, 0); return
    }
    if (stage.mode[0] === 'p') {
      const p = pilots.get(stage.mode.slice(1))
      if (p?.pos) {
        const now = p.pos.clone(), fwd = p.fwd
        cam.setPosition(now.clone().sub(fwd.clone().mulScalar(8 * view.zoom)).add(new pc.Vec3(0, 3 * view.zoom, 0))); cam.lookAt(now.add(fwd.clone().mulScalar(5))); return
      }
    }
    const r = new pc.Quat().setFromEulerAngles(view.pitch, view.yawOffset + TURN * t, 0)
    const dir = r.transformVector(new pc.Vec3(0, 0, 1), new pc.Vec3()), right = r.transformVector(new pc.Vec3(1, 0, 0), new pc.Vec3()), upv = r.transformVector(new pc.Vec3(0, 1, 0), new pc.Vec3())
    const goal = course.length ? home.target : up.length ? up.reduce((a, b) => a.clone().add(b), new pc.Vec3()).mulScalar(1 / up.length) : home.target
    orb.target.lerp(orb.target, goal, 1 - Math.exp(-dt / 1.5))
    const tanV = Math.tan(30 * Math.PI / 180) * 0.85, tanH = tanV * canvas.clientWidth / Math.max(1, canvas.clientHeight)
    let need = course.length || up.length ? 12 : home.dist
    for (const q of course.length ? course.concat(up) : up) { const v = q.clone().sub(orb.target), a = v.dot(right), b = v.dot(upv), c = v.dot(dir); need = Math.max(need, Math.abs(a) / tanH + c, Math.abs(b) / tanV + c) }
    need *= view.zoom; orb.dist += (need - orb.dist) * (1 - Math.exp(-dt / (need > orb.dist ? 1.0 : 2.5)))
    cam.setPosition(r.transformVector(new pc.Vec3(0, 0, orb.dist), new pc.Vec3()).add(orb.target)); cam.setRotation(r)
  }

  // --- the trail (as livepage.ts): an opaque, depth-writing band facing the camera, before the splats, thinning to its old
  // end and dithered out; broken where a frame has no position
  const trailMat = new pc.StandardMaterial()
  { const m = trailMat; m.useLighting = false; m.diffuse = new pc.Color(0, 0, 0); m.cull = pc.CULLFACE_NONE; m.emissiveVertexColor = true; m.emissive = new pc.Color(1, 1, 1)
    m.blendType = pc.BLEND_NONE; m.depthWrite = true; m.depthTest = true; m.opacityVertexColor = true; m.opacityVertexColorChannel = 'a'; m.opacityDither = pc.DITHER_BLUENOISE; m.update() }
  function drawTrail(mesh: pc.Mesh, pts: (number[] | null)[], c: pc.Color, eye: pc.Vec3, pxWorld: number) {
    const pos: number[] = [], col: number[] = [], nrm: number[] = [], idx: number[] = [], n = pts.length
    const tan = new pc.Vec3(), vw = new pc.Vec3(), side = new pc.Vec3(), a = new pc.Vec3(), b = new pc.Vec3(), q = new pc.Vec3(); let run = 0
    for (let k = 0; k < n; k++) {
      const p = pts[k]; if (!p) { run = 0; continue }
      const pa = pts[k - 1] ?? p, pb = pts[k + 1] ?? p
      q.set(p[0], p[1], p[2]); a.set(pa[0], pa[1], pa[2]); b.set(pb[0], pb[1], pb[2])
      tan.sub2(a, b); if (tan.lengthSq() < 1e-12) tan.set(1, 0, 0); tan.normalize(); vw.sub2(eye, q)
      const al = k / Math.max(1, n - 1), w = (1 + (TRAIL_PX - 1) * al) / 2 * pxWorld * vw.length()
      side.cross(tan, vw.normalize()); if (side.lengthSq() < 1e-12) side.set(0, 1, 0); side.normalize().mulScalar(w)
      pos.push(q.x + side.x, q.y + side.y, q.z + side.z, q.x - side.x, q.y - side.y, q.z - side.z); col.push(c.r, c.g, c.b, al, c.r, c.g, c.b, al); nrm.push(0, 1, 0, 0, 1, 0)
      if (run) { const i = pos.length / 3 - 2; idx.push(i - 2, i - 1, i, i - 1, i + 1, i) }
      run++
    }
    if (idx.length < 3) { emptyMesh(mesh); return }
    mesh.setPositions(pos); mesh.setNormals(nrm); mesh.setColors(col); mesh.setIndices(idx); mesh.update(pc.PRIMITIVE_TRIANGLES)
  }

  // --- the course line (as livepage.ts): a faint white dashed band, blended over the splats without depth
  const lineMesh = new pc.Mesh(app.graphicsDevice); lineMesh.clear(true, false); emptyMesh(lineMesh)
  { const m = new pc.StandardMaterial(); m.useLighting = false; m.diffuse = new pc.Color(0, 0, 0); m.cull = pc.CULLFACE_NONE; m.emissiveVertexColor = true; m.emissive = new pc.Color(1, 1, 1)
    m.opacityVertexColor = true; m.opacityVertexColorChannel = 'a'; m.blendType = pc.BLEND_NORMAL; m.depthWrite = false; m.depthTest = false; m.update()
    const e = new pc.Entity('course'); e.addComponent('render', { meshInstances: [new pc.MeshInstance(lineMesh, m)], layers: [pc.LAYERID_IMMEDIATE] }); app.root.addChild(e) }
  function drawLine(eye: pc.Vec3) {
    const n = line.length; if (!n) { emptyMesh(lineMesh); return }
    const pos: number[] = [], col: number[] = [], nrm: number[] = [], idx: number[] = [], tan = new pc.Vec3(), vw = new pc.Vec3(), side = new pc.Vec3()
    for (let k = 0; k < n; k++) {
      const q = line[k]; tan.sub2(line[(k - 1 + n) % n], line[(k + 1) % n]).normalize(); vw.sub2(eye, q)
      side.cross(tan, vw.normalize()).normalize().mulScalar(LINE_W / 2)
      const al = lineS[k] % (LINE_DASH + LINE_GAP) < LINE_DASH ? LINE_A : 0
      pos.push(q.x + side.x, q.y + side.y, q.z + side.z, q.x - side.x, q.y - side.y, q.z - side.z); col.push(1, 1, 1, al, 1, 1, 1, al); nrm.push(0, 1, 0, 0, 1, 0)
      const i = 2 * k, j = 2 * ((k + 1) % n); idx.push(i, i + 1, j, i + 1, j + 1, j)
    }
    lineMesh.setPositions(pos); lineMesh.setNormals(nrm); lineMesh.setColors(col); lineMesh.setIndices(idx); lineMesh.update(pc.PRIMITIVE_TRIANGLES)
  }

  // --- a pilot: sphere, trail and tag; drawn each frame from what the page last showed
  class Pilot implements StagePilot {
    color: string; col: pc.Color; pos: pc.Vec3 | null = null; state: PilotState = 'nopic'; fwd = new pc.Vec3(0, 0, -1); prev: pc.Vec3 | null = null
    mat = new pc.StandardMaterial(); dot = new pc.Entity('dot'); trail: pc.Mesh; trailE = new pc.Entity('trail'); pts: (number[] | null)[] = []; tag = document.createElement('div'); label = ''
    constructor(public stream: string, public slot: Slot) {
      this.color = css('--' + slot) || '#5b4bff'; this.col = hex(this.color)
      const m = this.mat; m.useLighting = false; m.diffuse = new pc.Color(0, 0, 0); m.emissive = this.col; m.update()
      this.dot.addComponent('render', { type: 'sphere', material: m, castShadows: false }); this.dot.setLocalScale(0.14, 0.14, 0.14); this.dot.enabled = false; app.root.addChild(this.dot)
      this.trail = new pc.Mesh(app.graphicsDevice); this.trail.clear(true, false); emptyMesh(this.trail)
      this.trailE.addComponent('render', { meshInstances: [new pc.MeshInstance(this.trail, trailMat)] }); app.root.addChild(this.trailE)
      this.tag.className = 'tag'; this.tag.style.setProperty('--c', this.color); this.tag.hidden = true; document.body.appendChild(this.tag)
    }
    show(pos: number[], trail: (number[] | null)[], state: PilotState, label: { name: string; flag: string; note: string }) {
      const now = new pc.Vec3(pos[0], pos[1], pos[2]), flat = new pc.Vec3(pos[0], 0, pos[2])
      if (this.prev) { const v = flat.clone().sub(this.prev); if (v.length() > 0.02) this.fwd.lerp(this.fwd, v.normalize(), 0.08).normalize() }
      this.prev = flat; this.pos = now; this.state = state; this.pts = trail
      this.dot.enabled = true; this.dot.setPosition(now)
      const key = [state, label.flag, label.name, label.note].join('|')
      if (key === this.label) return; this.label = key                 // the material and the tag only when they change
      this.mat.emissive = state === 'flying' ? this.col : cLost; this.mat.update()
      const b = document.createElement('b'); b.textContent = this.stream
      const who = Object.assign(document.createElement('span'), { textContent: (label.flag + ' ' + label.name).trim() })
      const note = Object.assign(document.createElement('small'), { textContent: label.note })
      this.tag.classList.toggle('lost', state !== 'flying'); this.tag.replaceChildren(b, ...(label.name ? [who] : []), ...(label.note ? [note] : []))
    }
    destroy() { this.dot.destroy(); this.trailE.destroy(); this.tag.remove() }
    hide() { this.pos = null; this.state = 'nopic'; this.pts = []; this.prev = null; this.dot.enabled = false; this.tag.hidden = true; emptyMesh(this.trail) }
    draw(eye: pc.Vec3, pxWorld: number) {
      if (!this.pos) return
      drawTrail(this.trail, this.pts, this.state === 'flying' ? this.col : cLost, eye, pxWorld)
      const s = cam.camera!.worldToScreen(this.pos, new pc.Vec3()), behind = cam.forward.dot(this.pos.clone().sub(eye)) < 0
      this.tag.hidden = behind; this.tag.style.left = s.x + 'px'; this.tag.style.top = s.y + 'px'
    }
  }

  let t = 0
  app.on('update', (dt: number) => {
    t += dt; cameraAt(t, dt)
    if (blendT < BLEND) { blendT = Math.min(BLEND, blendT + dt); const u = blendT / BLEND, k = u * u * (3 - 2 * u)
      cam.setPosition(new pc.Vec3().lerp(blendFrom.pos, cam.getPosition(), k)); cam.setRotation(new pc.Quat().slerp(blendFrom.rot, cam.getRotation(), k)) }
    const eye = cam.getPosition(), pxWorld = 2 * Math.tan(30 * Math.PI / 180) / Math.max(1, canvas.clientHeight)
    drawLine(eye); trailMat.setParameter('blueNoiseJitter', [Math.random(), Math.random(), Math.random(), Math.random()])
    for (const p of pilots.values()) p.draw(eye, pxWorld)
  })
  app.start()

  const json = (u: string) => fetch(u).then(r => { if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json() })
  const stage: Stage = {
    app, mode: 'orbit', onMode: () => {},
    async loadScene() {
      const ix = await json(base + 'index.json')
      if (ix.orbit?.target) { home.target.set(ix.orbit.target[0], ix.orbit.target[1], ix.orbit.target[2]); home.dist = ix.orbit.dist ?? home.dist; orb.init = false }
      const name = ix.lite && PHONE ? ix.lite : ix.scene
      await new Promise<void>((ok, bad) => {
        const a = new pc.Asset(name, 'gsplat', { url: `${base}scene/${name}.sog` }); app.assets.add(a)
        a.on('load', () => { const e = new pc.Entity('scene'); e.addComponent('gsplat', { asset: a }); app.root.addChild(e); ok() }); a.on('error', bad); app.assets.load(a)
      })
      return { title: ix.title }
    },
    setCourse(track) {                                       // the course line; it may come later or be replaced during the day
      if (!track?.length) { course = []; line = []; lineS = []; return }
      course = track.filter((_, k) => k % 8 === 0).map(q => new pc.Vec3(q[0], q[1], q[2]))
      line = track.map(q => new pc.Vec3(q[0], q[1], q[2])); lineS = [0]; for (let k = 1; k < line.length; k++) lineS.push(lineS[k - 1] + line[k].distance(line[k - 1]))
      const mid = (k: number) => { const v = track.map(q => q[k]); return (Math.min(...v) + Math.max(...v)) / 2 }, ys = track.map(q => q[1]).sort((a, b) => a - b)
      home.target.set(mid(0), ys[ys.length >> 1], mid(2)); orb.init = false
    },
    pilot(stream, slot) { let p = pilots.get(stream); if (!p || p.slot !== slot) { p?.destroy(); p = new Pilot(stream, slot); pilots.set(stream, p) } return p },
    setMode(m) { if (m === stage.mode) return; blendFrom.pos.copy(cam.getPosition()); blendFrom.rot.copy(cam.getRotation()); blendT = 0; stage.mode = m; stage.onMode(m) },
    clear() { for (const p of pilots.values()) p.hide() },
  }
  Object.assign(window, { ghostline: { app, pc, pilots, stage } })   // for checking the page from outside
  return stage
}
