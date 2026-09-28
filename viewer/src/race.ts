// FDF CUP 2026 R6 A-main semi-final: three pilots' DVR paths on the 3DGS scan, played back together.
// race.json (from each flight's poses60_m.json): the paths at the clip's 30 fps, the start of the race in clip
// time (from KANATA's official total), the finish gate and the laps.
import * as pc from 'playcanvas'
import { bakeSky } from './sky'

type Pilot = { name: string; color: string; video: string; end: number; crashed: boolean; finish: number | null; lap_ends: number[]; laps: number[]; total: number; holeshot: number; holeshot_at: number
  official: { pos: number; laps: number; time: string; gap: string }; pos: number[][] }
type Race = { title: string; fps: number; start: number; scene: string; laps: number; gate: { centre: number[] }; pilots: Pilot[]; track: number[][] }

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const QS = new URLSearchParams(location.search)
const DATA = import.meta.env.BASE_URL + (QS.get('data') || import.meta.env.VITE_RACE_DATA || 'race-sf-semifinal') + '/'   // VITE_RACE_DATA: the published folder (data/...)
const TRAIL = 2                                                    // seconds of trail behind each drone
const canvas = $<HTMLCanvasElement>('c'), status = $('status')
const app = new pc.Application(canvas, { mouse: new pc.Mouse(canvas), graphicsDeviceOptions: { antialias: true } })
app.setCanvasFillMode(pc.FILLMODE_FILL_WINDOW); app.setCanvasResolution(pc.RESOLUTION_AUTO)
window.addEventListener('resize', () => app.resizeCanvas())
const cam = new pc.Entity('cam'); cam.addComponent('camera', { clearColor: new pc.Color(0.91, 0.92, 0.94), fov: 50, nearClip: 0.2, farClip: 2000 })
app.root.addChild(cam)

// sky and scan
const skyImg = new Image(); skyImg.onload = () => { app.scene.skyboxMip = 0; app.scene.skybox = bakeSky(app.graphicsDevice, skyImg) }; skyImg.src = DATA + 'sky.jpg'
let scan: pc.Entity | null = null
function loadScene(name: string) {
  const a = new pc.Asset(name, 'gsplat', { url: `${DATA}scene/${name}.sog` }); app.assets.add(a)
  a.on('load', () => { const e = new pc.Entity('scan'); e.addComponent('gsplat', { asset: a }); app.root.addChild(e); scan = e; status.hidden = true })
  a.on('error', (err: string) => { status.textContent = 'scene failed: ' + err })
  app.assets.load(a)
}

// a failed load says so on the page (it used to sit on 'loading…' with the reason only in the console)
const race: Race = await fetch(DATA + 'race.json').then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json() })
  .catch((e: Error) => { status.textContent = `could not load ${DATA}race.json: ${e.message}`; throw e })
{ // the track's length: the closed average line of KANATA's three laps
  const tr = race.track ?? []; let len = 0; for (let i = 0; i < tr.length; i++) { const a = tr[i], b = tr[(i + 1) % tr.length]; len += Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) }
  const info = document.createElement('small'); info.textContent = `track ${len.toFixed(0)} m · ${race.laps} laps`
  const sub = document.createElement('small'); sub.textContent = 'Flight lines are estimated from each pilot\'s DVR video by image analysis and are approximate, not measured positions.'
  $('title').replaceChildren(document.createTextNode(race.title), info, sub) }
loadScene(race.scene)
const P = race.pilots, FPS = race.fps
const T_END = Math.max(...P.map(p => p.end))                      // clip time of the last solved frame of anyone
const AUDIO_LEAD = 3                                               // audio.m4a starts 3 s before the clip: the countdown
const T0 = race.start - 2 - AUDIO_LEAD                             // playback opens with the countdown (clip time < 0: first frames held)
// position at clip time t: frames interpolated; before the path starts, its first frame; after its end, its last
function at(p: Pilot, t: number): pc.Vec3 {
  const f = Math.max(0, Math.min(p.pos.length - 1, t * FPS)), i = Math.floor(f), j = Math.min(p.pos.length - 1, i + 1), u = f - i
  const a = p.pos[i], b = p.pos[j]; return new pc.Vec3(a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u)
}
// speed at clip time t from the path, km/h: the distance over +-0.1 s (the path's own frame jitter is a few cm)
function kmh(p: Pilot, t: number) { if (t > p.end || t < 0.1) return 0; return at(p, Math.min(p.end, t + 0.1)).distance(at(p, t - 0.1)) / 0.2 * 3.6 }
const hex = (h: string) => new pc.Color(parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255)
const grey = new pc.Color(0.55, 0.57, 0.6)
const drones = P.map(p => {
  const m = new pc.StandardMaterial(); m.diffuse = new pc.Color(0, 0, 0); m.emissive = hex(p.color); m.useLighting = false; m.update()
  const e = new pc.Entity(p.name); e.addComponent('render', { type: 'sphere', material: m, castShadows: false }); e.setLocalScale(0.7, 0.7, 0.7)
  app.root.addChild(e)
  const tag = document.createElement('div'); tag.className = 'tag'; tag.style.background = p.color; document.body.appendChild(tag)
  return { p, e, m, tag, col: hex(p.color) }
})

// the pilots' own DVR feeds (the broadcast's quad view, one quadrant each, 4:3), played on the same clock
const speedTags: HTMLDivElement[] = []                                // each pilot's speed, over its DVR tile
const videos = P.map(p => {
  const tile = document.createElement('div'); tile.className = 'tile'; tile.style.borderTopColor = p.color
  const v = document.createElement('video'); v.src = DATA + p.video; v.muted = true; v.playsInline = true; v.preload = 'auto'
  const name = document.createElement('span'); name.textContent = p.name; name.style.background = p.color
  const sp = document.createElement('div'); sp.className = 'kmh'; speedTags.push(sp)
  tile.append(v, name, sp); $('dvr').appendChild(tile); return v
})
// the broadcast's sound, on the same clock
const audio = new Audio(DATA + 'audio.m4a'); audio.preload = 'auto'   // on by default: playing always starts from a click (Play / space), which lets the browser play sound
const mediaTime = (v: HTMLMediaElement) => v === audio ? t + AUDIO_LEAD : Math.max(0, t)   // the audio file starts AUDIO_LEAD s early
const soundBtn = $<HTMLButtonElement>('soundbtn')
soundBtn.onclick = () => { audio.muted = !audio.muted; $('snd-on').toggleAttribute('hidden', audio.muted); $('snd-off').toggleAttribute('hidden', !audio.muted); soundBtn.setAttribute('aria-pressed', String(!audio.muted)); soundBtn.blur() }
function syncVideos() {
  for (const v of [...videos, audio]) {
    const mt = mediaTime(v), run = playing && !seeking && (v === audio || t >= 0)   // videos hold their first frame before the clip
    if (run) {
      v.playbackRate = speed; if (v.paused) v.play().catch(() => {})
      if (Math.abs(v.currentTime - mt) > 0.15 && !v.seeking) v.currentTime = mt
    } else {
      if (!v.paused) v.pause()
      if (Math.abs(v.currentTime - mt) > 0.02 && !v.seeking) v.currentTime = mt
    }
  }
}

// trails: a ribbon per pilot, turned to face the camera and a constant few pixels wide, fading to nothing at its old
// end (WebGL lines are one pixel, and the immediate-mode ones cannot be transparent - fading them meant mixing in
// white, which drew white lines over the scan)
const TRAIL_PX = 4
function ribbonMesh() {
  // created with a triangle of every stream: the material's shader is built from the mesh's first streams, and an
  // empty mesh gave one without vertex colours (white, never fading)
  const mesh = new pc.Mesh(app.graphicsDevice); mesh.clear(true, false)
  mesh.setPositions([0, 0, 0, 0, 0, 0, 0, 0, 0]); mesh.setNormals([0, 1, 0, 0, 1, 0, 0, 1, 0]); mesh.setColors(new Array(12).fill(0)); mesh.setIndices([0, 1, 2]); mesh.update(pc.PRIMITIVE_TRIANGLES)
  const mat = new pc.StandardMaterial(); mat.useLighting = false; mat.diffuse = new pc.Color(0, 0, 0); mat.cull = pc.CULLFACE_NONE
  mat.emissiveVertexColor = true; mat.emissive = new pc.Color(1, 1, 1); mat.opacityVertexColor = true; mat.opacityVertexColorChannel = 'a'
  mat.blendType = pc.BLEND_NORMAL; mat.depthWrite = false; mat.depthTest = false; mat.update()
  const e = new pc.Entity('ribbon'); e.addComponent('render', { meshInstances: [new pc.MeshInstance(mesh, mat)], layers: [pc.LAYERID_IMMEDIATE] }); app.root.addChild(e)   // after the splats
  return mesh
}
// a band through pts, turned to face the eye and widthPx pixels wide wherever it is; alpha per point
function ribbon(mesh: pc.Mesh, pts: pc.Vec3[], c: pc.Color, alpha: (k: number) => number, eye: pc.Vec3, pxWorld: number, widthPx: number, closed = false) {
  if (pts.length < 2) { mesh.setPositions([0, 0, 0, 0, 0, 0, 0, 0, 0]); mesh.setNormals([0, 1, 0, 0, 1, 0, 0, 1, 0]); mesh.setColors(new Array(12).fill(0)); mesh.setIndices([0, 1, 2]); mesh.update(pc.PRIMITIVE_TRIANGLES); return }
  const pos: number[] = [], col: number[] = [], nrm: number[] = [], idx: number[] = [], n = pts.length
  const tan = new pc.Vec3(), view = new pc.Vec3(), side = new pc.Vec3()
  pts.forEach((q, k) => {
    const a_ = closed ? pts[(k - 1 + n) % n] : pts[Math.max(0, k - 1)], b_ = closed ? pts[(k + 1) % n] : pts[Math.min(n - 1, k + 1)]
    tan.sub2(a_, b_).normalize(); view.sub2(eye, q)
    const w = widthPx / 2 * pxWorld * view.length(); side.cross(tan, view.normalize()).normalize().mulScalar(w); const al = alpha(k)
    pos.push(q.x + side.x, q.y + side.y, q.z + side.z, q.x - side.x, q.y - side.y, q.z - side.z)
    col.push(c.r, c.g, c.b, al, c.r, c.g, c.b, al); nrm.push(0, 1, 0, 0, 1, 0)
    if (k) { const i = 2 * k; idx.push(i - 2, i - 1, i, i - 1, i + 1, i) }
  })
  if (closed) { const i = 2 * n; idx.push(i - 2, i - 1, 0, i - 1, 1, 0) }
  mesh.setPositions(pos); mesh.setNormals(nrm); mesh.setColors(col); mesh.setIndices(idx); mesh.update(pc.PRIMITIVE_TRIANGLES)
}
const trails = drones.map(() => ribbonMesh())
function updateTrail(mesh: pc.Mesh, p: Pilot, tc: number, c: pc.Color, eye: pc.Vec3, pxWorld: number) {
  // the last TRAIL seconds of the path up to its end, faded by age: after a crash it drains away instead of staying
  const n = Math.round(TRAIL * FPS), pts: pc.Vec3[] = [], age: number[] = []
  for (let k = 0; k <= n && tc - k / FPS >= 0; k++) { const s = tc - k / FPS; if (s > p.end) continue; pts.push(at(p, s)); age.push(k / n) }
  ribbon(mesh, pts, c, k => 1 - age[k], eye, pxWorld, TRAIL_PX)
}
// the track: KANATA's three laps averaged (tools/dvr/make_race.py)
// resampled every 0.25 m for the dashes (1.25 m on / 0.75 m off); race.json's line is already smoothed (0.5 m), and
// smoothing it again here over 3 m flattened the ladder's spiral (loops of 1.8 m radius)
const trackPts = (() => {
  const sm = (race.track ?? []).map(q => new pc.Vec3(q[0], q[1], q[2])), n = sm.length; if (n < 3) return [] as pc.Vec3[]
  const out: pc.Vec3[] = []; let carry = 0
  for (let i = 0; i < n; i++) { const a = sm[i], b = sm[(i + 1) % n], L = a.distance(b); if (L < 1e-6) continue; let d = carry; for (; d < L; d += 0.25) out.push(new pc.Vec3().lerp(a, b, d / L)); carry = d - L }
  return out
})()
const trackMesh = ribbonMesh(), trackCol = new pc.Color(1, 1, 1)
const trackAlpha = (k: number) => ((k * 0.25) % 2 < 1.25 ? 0.18 : 0)
const showTrack = $<HTMLInputElement>('showtrack'), trackBtn = $<HTMLButtonElement>('trackbtn')
trackBtn.onclick = () => { showTrack.checked = !showTrack.checked; trackBtn.classList.toggle('on', showTrack.checked); trackBtn.setAttribute('aria-pressed', String(showTrack.checked)); trackBtn.blur() }

// timeline state
let t = T0, playing = false, speed = 1
const play = $<HTMLButtonElement>('play')
const setPlaying = (v: boolean) => { playing = v; play.textContent = v ? '❚❚ Pause' : '▶ Play' }
play.onclick = () => { if (t >= T_END - 0.01) t = T0; setPlaying(!playing) }
const speedSel = $<HTMLSelectElement>('speed'); speedSel.onchange = () => { speed = Number(speedSel.value); refreshSpeed() }
const refreshSpeed = segment($('speedseg'), speedSel, o => [document.createTextNode(o.text + '×')]); refreshSpeed()
window.addEventListener('keydown', e => { if (e.key === ' ') { play.click(); e.preventDefault() }
  if (e.key === 'ArrowRight') t = Math.min(T_END, t + (e.shiftKey ? 5 : 1)); if (e.key === 'ArrowLeft') t = Math.max(T0, t - (e.shiftKey ? 5 : 1)) })
const raceTime = (tc: number) => tc - race.start

// live standings: finished first by finishing time, then by laps done, then who crossed the line earlier
function standings(tc: number) {
  return P.map(p => {
    const done = p.lap_ends.filter(x => x <= tc).length, finished = p.finish !== null && tc >= p.finish
    const out = tc > p.end && !finished
    return { p, done, finished, out, last: done ? p.lap_ends[done - 1] : -1 }
  }).sort((a, b) => (Number(b.finished) - Number(a.finished)) || (b.done - a.done) || (a.last - b.last))
}
const fmt = (s: number) => s >= 60 ? `${Math.floor(s / 60)}:${(s % 60).toFixed(3).padStart(6, '0')}` : s.toFixed(3)
function drawBoard(tc: number) {
  // the live order, one line per pilot beside its timeline row (rows stay in race.json's order, the number is the place)
  const rt = raceTime(tc), place = new Map(standings(tc).map((s, k) => [s.p.name, { ...s, k: k + 1 }]))
  const span = (cls: string, text: string) => { const e = document.createElement('span'); e.className = cls; e.textContent = text; return e }
  $('info').replaceChildren(...P.map(p => {
    const s = place.get(p.name)!, row = document.createElement('div'); row.className = 'pl' + (s.out ? ' out' : '')
    const cur = s.finished ? 'FINISH' : s.out ? 'OUT' : rt < 0 ? 'START' : `LAP ${Math.min(s.done + 1, race.laps)}/${race.laps}`
    // finished: the total; out: the time of its last lap; racing: the lap in progress
    const time = s.finished ? fmt(p.total) : s.out ? (s.done ? fmt(s.last - race.start) : '') : rt < 0 ? '' : fmt(Math.min(tc, p.end) - (s.done ? s.last : race.start))
    const name = span('name', p.name); const dot = document.createElement('i'); dot.style.background = s.out ? '#8c9199' : p.color; name.prepend(dot)
    row.append(span('pos', String(s.k)), name, span('lap', cur), span('time', time))
    const v = s.out || s.finished && tc > p.end ? 0 : kmh(p, tc); speedTags[P.indexOf(p)].textContent = v ? `${v.toFixed(0).padStart(3)} km/h` : ''
    return row
  }))
  const unit = document.createElement('small'); unit.textContent = 's'
  $('clock').replaceChildren(document.createTextNode(rt < 0 ? '−' + (-rt).toFixed(3) : rt.toFixed(3)), unit)
}
// timeline canvas: one row per pilot with its lap ends, the playhead; drag to seek
const tl = $<HTMLCanvasElement>('tl')
function drawTimeline(tc: number) {
  const r = tl.getBoundingClientRect(), dpr = devicePixelRatio, W = Math.round(r.width * dpr), H = Math.round(r.height * dpr)
  if (tl.width !== W || tl.height !== H) { tl.width = W; tl.height = H }
  const g = tl.getContext('2d')!; g.clearRect(0, 0, W, H)
  const L = 4 * dpr, R = W - 8 * dpr, x = (tt: number) => L + (tt - T0) / (T_END - T0) * (R - L), rowH = H / P.length
  g.font = `600 ${12 * dpr}px Inter, sans-serif`; g.textBaseline = 'middle'
  P.forEach((p, k) => {
    const y = rowH * (k + 0.5)
    g.fillStyle = '#e3e5ea'; g.fillRect(L, y - 3 * dpr, R - L, 6 * dpr)
    g.fillStyle = p.color; g.globalAlpha = 0.35; g.fillRect(x(race.start), y - 3 * dpr, x(Math.min(p.end, T_END)) - x(race.start), 6 * dpr); g.globalAlpha = 1
    g.fillStyle = '#6b7180'; g.fillRect(x(p.holeshot_at) - 1 * dpr, y - 6 * dpr, 2 * dpr, 12 * dpr)
    g.font = `600 ${11 * dpr}px "Barlow Condensed", sans-serif`; g.fillText(`HS ${p.holeshot.toFixed(2)}`, x(p.holeshot_at) + 3 * dpr, y + 9 * dpr)
    for (const [n, e] of p.lap_ends.entries()) {
      g.fillStyle = p.color; g.fillRect(x(e) - 1.5 * dpr, y - 9 * dpr, 3 * dpr, 18 * dpr)
      g.font = `600 ${11 * dpr}px "Barlow Condensed", sans-serif`; g.fillStyle = '#16181d'; g.fillText(`L${n + 1} ${p.laps[n].toFixed(2)}`, x(e) + 4 * dpr, y - 9 * dpr)
      g.font = `600 ${12 * dpr}px Inter, sans-serif`
    }
    if (p.crashed) { g.fillStyle = '#6b7180'; g.fillText('✕', x(p.end) - 4 * dpr, y) }
  })
  g.strokeStyle = '#16181d'; g.lineWidth = 1 * dpr; g.setLineDash([4 * dpr, 4 * dpr]); g.beginPath(); g.moveTo(x(race.start), 0); g.lineTo(x(race.start), H); g.stroke(); g.setLineDash([])
  g.fillStyle = '#16181d'; g.fillRect(x(tc) - 1 * dpr, 0, 2 * dpr, H)
}
let seeking = false
const seek = (e: PointerEvent) => { const r = tl.getBoundingClientRect(); const L = 4, R = r.width - 8
  t = Math.max(T0, Math.min(T_END, T0 + (e.clientX - r.left - L) / (R - L) * (T_END - T0))) }
tl.addEventListener('pointerdown', e => { seeking = true; tl.setPointerCapture(e.pointerId); seek(e) })
tl.addEventListener('pointermove', e => { if (seeking) seek(e) })
// released anywhere (outside the timeline too) or cancelled: the drag ends
const endSeek = () => { seeking = false }
tl.addEventListener('pointerup', endSeek); tl.addEventListener('pointercancel', endSeek); tl.addEventListener('lostpointercapture', endSeek); window.addEventListener('pointerup', endSeek)

// camera, worked out for the whole race up front (easing it live lagged and let drones slip out of frame):
// the yaw turns with race time, the target is the three drones' middle smoothed both ways, and the distance is the
// least that holds all three inside the free part of the screen (not under the DVR column or the timeline), eased
// the same way, kept within the scanned course (fog beyond).
const box = (() => { const all = P.flatMap(p => p.pos), m = 10
  return { min: [0, 1, 2].map(k => Math.min(...all.map(q => q[k])) - m), max: [0, 1, 2].map(k => Math.max(...all.map(q => q[k])) + (k === 1 ? 40 : m)) } })()   // the fog is at the sides; above is open
const FOV = 50, PITCH0 = -18, TURN = 6                                 // vertical fov of the free area; degrees a race second
const view = { yawOffset: 30, topYaw: 0, pitch: PITCH0, zoom: 1 }
const free = { x: 0, y: 0, w: 1, h: 1, W: 1, H: 1 }                    // the free rect in CSS px, and the canvas size
function measureFree() {
  const W = canvas.clientWidth, H = canvas.clientHeight, bar = $('bar').getBoundingClientRect(), dvr = $('dvr')
  const right = getComputedStyle(dvr).display !== 'none' ? dvr.getBoundingClientRect().left : W   // fixed elements have no offsetParent
  const top = Math.round($('title').getBoundingClientRect().bottom)                                // below the title panel too
  const next = { x: 0, y: top, w: Math.max(100, Math.round(right)), h: Math.max(100, Math.round(bar.top) - top), W, H }
  const changed = (Object.keys(next) as (keyof typeof free)[]).some(k => next[k] !== free[k]); Object.assign(free, next); return changed
}
measureFree()
// off-centre projection: the free rect's centre is the optical axis and its height spans FOV
cam.camera!.calculateProjection = (m: pc.Mat4) => {
  const near = cam.camera!.nearClip, far = cam.camera!.farClip, s_ = 2 * near * Math.tan(FOV * Math.PI / 360) / free.h
  const cx = free.x + free.w / 2, cy = free.y + free.h / 2
  m.setFrustum((0 - cx) * s_, (free.W - cx) * s_, (cy - free.H) * s_, (cy - 0) * s_, near, far)
}
// camera modes: orbit (the planned turn), top (the whole course from above), free, or following one pilot; keys 1-6
const mode = $<HTMLSelectElement>('cammode')
mode.replaceChildren(new Option('Orbit', 'orbit'), new Option('Top', 'top'), new Option('Free', 'free'), ...P.map((p, k) => new Option(p.name, 'p' + k)))   // p<k>: follow pilot k
// the controls as button groups over the hidden select / checkbox (the code reads those as before)
function segment(host: HTMLElement, sel: HTMLSelectElement, label: (o: HTMLOptionElement, i: number) => Node[]) {
  const btns = Array.from(sel.options).map((o, i) => { const b = document.createElement('button'); b.append(...label(o, i))
    b.onclick = () => { sel.selectedIndex = i; sel.onchange?.(new Event('change')); b.blur() }; return b })
  host.replaceChildren(...btns)
  return () => btns.forEach((b, i) => b.classList.toggle('on', i === sel.selectedIndex))
}
const camLabel = (o: HTMLOptionElement) => { if (o.value[0] !== 'p') return [document.createTextNode(o.text)]
  const dot = document.createElement('i'); dot.style.background = P[Number(o.value.slice(1))].color; return [dot, document.createTextNode(o.text)] }
const refreshCam = segment($('camseg'), mode, camLabel)
window.addEventListener('keydown', e => { const k = Number(e.key); if (k >= 1 && k <= mode.options.length) { mode.selectedIndex = k - 1; mode.onchange!(new Event('change')) } })
const auto = { get checked() { return mode.value === 'orbit' } }
const yawAt = (tc: number) => view.yawOffset + (auto.checked ? TURN * (tc - T0) : 0)
const DT = 0.1, NS = Math.ceil((T_END - T0) / DT) + 1
let planT: pc.Vec3[] = [], planD: number[] = []
const gauss = (xs: number[], sigma: number) => { const r = Math.ceil(3 * sigma / DT), w = Array.from({ length: 2 * r + 1 }, (_, k) => Math.exp(-0.5 * ((k - r) * DT / sigma) ** 2))
  return xs.map((_, i) => { let a = 0, b = 0; for (let k = -r; k <= r; k++) { const j = Math.min(xs.length - 1, Math.max(0, i + k)); a += xs[j] * w[k + r]; b += w[k + r] } return a / b }) }
function plan() {
  const tanV = Math.tan(FOV * Math.PI / 360) * 0.85, tanH = tanV * free.w / free.h      // 15% margin inside the free rect
  // who the frame holds: everyone still racing or finished; a pilot who went out (crashed before the finish) drops out
  const inFrame = (p: Pilot, tc: number) => !(tc > p.end && p.finish === null)
  const mids = Array.from({ length: NS }, (_, i) => { const tc = T0 + i * DT; const q = P.filter(p => inFrame(p, tc)); return (q.length ? q : P).map(p => at(p, Math.min(tc, p.end))) })
  const avg = (k: 'x' | 'y' | 'z') => gauss(mids.map(q => q.reduce((s_, v) => s_ + v[k], 0) / q.length), 0.8)
  const cx = avg('x'), cy = avg('y'), cz = avg('z')
  planT = cx.map((x, i) => new pc.Vec3(x, cy[i], cz[i]))
  const need: number[] = [], room: number[] = []
  for (let i = 0; i < NS; i++) {
    const r = new pc.Quat().setFromEulerAngles(view.pitch, yawAt(T0 + i * DT), 0)
    const dir = r.transformVector(new pc.Vec3(0, 0, 1), new pc.Vec3()), right = r.transformVector(new pc.Vec3(1, 0, 0), new pc.Vec3()), up = r.transformVector(new pc.Vec3(0, 1, 0), new pc.Vec3())
    // never lower than 8 m over the lowest path (the ground): up close at a shallow pitch it went through a roof
    let d = Math.max(12, dir.y > 1e-3 ? (box.min[1] + 10 + 8 - planT[i].y) / dir.y : 12)
    for (const q of mids[i]) { const v = q.clone().sub(planT[i]); d = Math.max(d, Math.abs(v.dot(right)) / tanH + v.dot(dir), Math.abs(v.dot(up)) / tanV + v.dot(dir)) }
    need.push(d * view.zoom)
    let rm = 1e9; const o = [planT[i].x, planT[i].y, planT[i].z], dv = [dir.x, dir.y, dir.z]
    for (let k = 0; k < 3; k++) { if (dv[k] > 1e-6) rm = Math.min(rm, (box.max[k] - o[k]) / dv[k]); else if (dv[k] < -1e-6) rm = Math.min(rm, (box.min[k] - o[k]) / dv[k]) }
    room.push(rm)
  }
  const w = Math.round(1 / DT), dil = need.map((_, i) => Math.max(...need.slice(Math.max(0, i - w), i + w + 1)))
  planD = gauss(dil, 0.6).map((x, i) => Math.max(6, Math.min(room[i], Math.max(x, need[i]))))
}
plan()
// free: a camera of its own around a pivot - left drag turns, right or shift drag pans, the wheel dollies; starts
// from wherever the view was, and time does not move it
const freeCam = { pivot: new pc.Vec3(), yaw: 0, pitch: -30, dist: 40 }
function enterFree() {
  // yaw and pitch read off the view direction rather than the euler angles, whose flip near straight down turned the
  // top view into one looking up from under the ground; looking (nearly) straight down, the heading is the screen's up
  // the pivot is where the view meets the ground (the lowest flown height), so turning orbits what is on screen
  const fwd = cam.forward.clone(), up = cam.up.clone(), eye = cam.getPosition(), dy = topView.cy - eye.y
  freeCam.dist = fwd.y < -0.05 && dy < 0 ? Math.max(5, Math.min(200, dy / fwd.y)) : 30
  freeCam.pivot.copy(eye).add(fwd.clone().mulScalar(freeCam.dist))
  const h = fwd.y < -0.99 ? up : fwd.y > 0.99 ? up.mulScalar(-1) : fwd
  freeCam.yaw = Math.atan2(-h.x, -h.z) * 180 / Math.PI; freeCam.pitch = Math.max(-89, Math.min(89, Math.asin(Math.max(-1, Math.min(1, fwd.y))) * 180 / Math.PI))
}
function cameraFree() {
  const r = new pc.Quat().setFromEulerAngles(freeCam.pitch, freeCam.yaw, 0)
  cam.setPosition(r.transformVector(new pc.Vec3(0, 0, freeCam.dist), new pc.Vec3()).add(freeCam.pivot)); cam.setRotation(r)
}
let lastMode = mode.value
mode.onchange = () => {
  mode.blur(); if (mode.value === lastMode) return
  // the orbit's heading now, kept across the switch (yawAt reads the new mode, so it is worked out from lastMode)
  const yaw = view.yawOffset + (lastMode === 'orbit' ? TURN * (t - T0) : 0)
  startBlend(); refreshCam(); if (mode.value === 'free') enterFree()
  view.yawOffset = mode.value === 'orbit' ? yaw - TURN * (t - T0) : yaw; lastMode = mode.value; plan()
}
refreshCam()
let drag: { x: number; y: number } | null = null
canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId) })
canvas.addEventListener('pointerup', () => { drag = null })
canvas.addEventListener('pointermove', e => { if (!drag) return
  if (mode.value === 'free') {
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag = { x: e.clientX, y: e.clientY }
    if (e.buttons & 2 || e.shiftKey) { const r = new pc.Quat().setFromEulerAngles(freeCam.pitch, freeCam.yaw, 0), k = freeCam.dist * 0.0015
      freeCam.pivot.sub(r.transformVector(new pc.Vec3(dx * k, 0, 0), new pc.Vec3())).add(r.transformVector(new pc.Vec3(0, dy * k, 0), new pc.Vec3())) }
    else { freeCam.yaw -= dx * 0.3; freeCam.pitch = Math.max(-89, Math.min(89, freeCam.pitch - dy * 0.3)) }
    return
  }
  if (mode.value === 'top') { view.topYaw -= (e.clientX - drag.x) * 0.3; drag = { x: e.clientX, y: e.clientY }; return } view.yawOffset -= (e.clientX - drag.x) * 0.3; view.pitch = Math.max(-85, Math.min(-5, view.pitch - (e.clientY - drag.y) * 0.3)); drag = { x: e.clientX, y: e.clientY }; plan() })
canvas.addEventListener('contextmenu', e => e.preventDefault())
canvas.addEventListener('wheel', e => { if (mode.value === 'free') { freeCam.dist = Math.max(2, Math.min(400, freeCam.dist * Math.exp(e.deltaY * 0.001))); e.preventDefault(); return } view.zoom = Math.max(0.4, Math.min(3, view.zoom * Math.exp(e.deltaY * 0.001))); plan(); e.preventDefault() }, { passive: false })
const topView = (() => {
  const all = P.flatMap(p => p.pos), cx = all.reduce((a, q) => a + q[0], 0) / all.length, cz = all.reduce((a, q) => a + q[2], 0) / all.length
  // turned so the line from the start pads to the timing gate runs left to right across the screen
  const pads = P.map(p => at(p, race.start)), sx = pads.reduce((a, q) => a + q.x, 0) / pads.length, sz = pads.reduce((a, q) => a + q.z, 0) / pads.length
  const vx = race.gate.centre[0] - sx, vz = race.gate.centre[2] - sz
  // searched rather than derived: the yaw whose screen-right best follows start -> gate (a closed form was 30 deg out)
  const vl = Math.hypot(vx, vz); let yaw = 0, best = -2
  for (let y = 0; y < 360; y += 0.5) { const rt = new pc.Quat().setFromEulerAngles(-89.9, y, 0).transformVector(new pc.Vec3(1, 0, 0), new pc.Vec3()); const c = (rt.x * vx + rt.z * vz) / vl; if (c > best) { best = c; yaw = y } }
  return { cx, cz, cy: Math.min(...all.map(q => q[1])), yaw, all }
})()
function cameraTop() {
  const r = new pc.Quat().setFromEulerAngles(-89.9, topView.yaw + view.topYaw, 0)
  const right = r.transformVector(new pc.Vec3(1, 0, 0), new pc.Vec3()), up = r.transformVector(new pc.Vec3(0, 1, 0), new pc.Vec3())
  const tanV = Math.tan(FOV * Math.PI / 360) * 0.9, tanH = tanV * free.w / free.h; let h = 20
  for (const q of topView.all) { const v = new pc.Vec3(q[0] - topView.cx, 0, q[2] - topView.cz); h = Math.max(h, Math.abs(v.dot(right)) / tanH, Math.abs(v.dot(up)) / tanV) }
  cam.setPosition(topView.cx, topView.cy + h * view.zoom, topView.cz); cam.setRotation(r)
}
function cameraFollow(p: Pilot, tc: number) {
  // behind and above the drone, looking a little ahead along where it is going; the heading comes from the path just
  // before and after (not a lagging filter), so the camera turns as the drone does
  const tt = Math.min(tc, p.end), now = at(p, tt), fwd = at(p, Math.min(p.end, tt + 0.25)).sub(at(p, Math.max(0, tt - 0.35)))
  fwd.y = 0; if (fwd.length() < 0.3) fwd.set(0, 0, -1); fwd.normalize()
  const eye = now.clone().sub(fwd.clone().mulScalar(8 * view.zoom)).add(new pc.Vec3(0, 3 * view.zoom, 0))
  cam.setPosition(eye); cam.lookAt(now.clone().add(fwd.clone().mulScalar(5)))
}
function cameraAt(tc: number) {
  if (mode.value === 'free') return cameraFree()
  if (mode.value === 'top') return cameraTop()
  if (mode.value[0] === 'p') return cameraFollow(P[Number(mode.value.slice(1))], tc)
  const f = Math.max(0, Math.min(NS - 1, (tc - T0) / DT)), i = Math.floor(f), j = Math.min(NS - 1, i + 1), u = f - i
  const target = new pc.Vec3().lerp(planT[i], planT[j], u), dist = planD[i] + (planD[j] - planD[i]) * u
  const r = new pc.Quat().setFromEulerAngles(view.pitch, yawAt(tc), 0)
  cam.setPosition(r.transformVector(new pc.Vec3(0, 0, dist), new pc.Vec3()).add(target)); cam.setRotation(r)
}

const tmpA = new pc.Vec3(), tmpB = new pc.Vec3()
// a camera switch glides from where the camera was instead of cutting
const BLEND = 0.7, blendFrom = { pos: new pc.Vec3(), rot: new pc.Quat() }
let blendT = BLEND
function startBlend() { blendFrom.pos.copy(cam.getPosition()); blendFrom.rot.copy(cam.getRotation()); blendT = 0 }
// PlayCanvas's CPU splat sort drops a request made while the previous one is still running, yet records the view as
// sorted; a camera that then stays still (top, paused) keeps the order of an earlier pose, which smears dark haze over
// the ground. So once the camera settles, one more sort is asked for directly, on a frame when the sorter is idle (so it
// is not dropped too). Turning the camera to trigger it showed as a shake.
const lastPos = new pc.Vec3(), lastRot = new pc.Quat()
let stillFor = 0, resorted = false
function resortWhenIdle() {
  let sent = false
  ;(app.renderer as any).gsplatDirector?.camerasMap.forEach((cd: any) => cd.layersMap.forEach((ld: any) => {
    const m = ld.gsplatManager; if (m && (!m.cpuSorter || m.cpuSorter.jobsInFlight === 0)) { m.sortNeeded = true; sent = true }
  }))
  return sent
}
app.on('update', (dt: number) => {
  if (playing && !seeking) { t += dt * speed; if (t >= T_END) { t = T_END; setPlaying(false) } }
  if (measureFree()) plan()                                             // the layout settled or the window changed
  cameraAt(t)
  if (blendT < BLEND) {
    blendT = Math.min(BLEND, blendT + dt); const u = blendT / BLEND, e = u * u * (3 - 2 * u)
    cam.setPosition(new pc.Vec3().lerp(blendFrom.pos, cam.getPosition(), e)); cam.setRotation(new pc.Quat().slerp(blendFrom.rot, cam.getRotation(), e))
  }
  const moved = cam.getPosition().distance(lastPos) > 1e-4 || Math.abs(Math.abs(cam.getRotation().dot(lastRot)) - 1) > 1e-9
  lastPos.copy(cam.getPosition()); lastRot.copy(cam.getRotation())
  stillFor = moved ? 0 : stillFor + dt; if (moved) resorted = false
  if (!resorted && stillFor >= 0.2) resorted = resortWhenIdle()
  const eye = cam.getPosition(), pxWorld = 2 * Math.tan(FOV * Math.PI / 360) / free.h   // world size of one pixel at unit distance
  ribbon(trackMesh, showTrack.checked ? trackPts : [], trackCol, trackAlpha, eye, pxWorld, 6, true)
  for (const [n, d] of drones.entries()) {
    const tc = Math.min(t, d.p.end), now = at(d.p, tc), gone = t > d.p.end
    d.e.setPosition(now); d.m.emissive = gone ? grey : d.col; d.m.update()
    updateTrail(trails[n], d.p, t, gone ? grey : d.col, eye, pxWorld)
    const s_ = cam.camera!.worldToScreen(now, tmpA), behind = cam.forward.dot(tmpB.copy(now).sub(eye)) < 0
    d.tag.style.display = behind ? 'none' : ''; d.tag.style.left = s_.x + 'px'; d.tag.style.top = s_.y + 'px'; d.tag.style.background = gone ? '#8c9199' : d.p.color
    const done = d.p.lap_ends.filter(x => x <= t).length
    d.tag.textContent = d.p.name; const sm = document.createElement('small')
    sm.textContent = d.p.finish !== null && t >= d.p.finish ? 'FINISH' : gone ? 'OUT' : raceTime(t) < 0 ? '' : `L${Math.min(done + 1, race.laps)}`
    d.tag.appendChild(sm)
  }
  drawBoard(t); drawTimeline(t); syncVideos()
})
// reset: the time back to before the start, paused; the orbit camera with nothing turned or zoomed; 1x
$<HTMLButtonElement>('resetbtn').onclick = e => {
  (e.currentTarget as HTMLButtonElement).blur(); setPlaying(false); t = T0
  Object.assign(view, { yawOffset: 30, topYaw: 0, pitch: PITCH0, zoom: 1 })
  speedSel.value = '1'; speedSel.onchange!(new Event('change'))
  startBlend(); mode.value = 'orbit'; lastMode = 'orbit'; refreshCam(); plan()
}
if (import.meta.env.DEV) Object.assign(window, { raceDebug: { cam, P, race, at, pc, freeCam, mode, get scan() { return scan } } })   // for checking the page from outside (dev server only)
app.start()
