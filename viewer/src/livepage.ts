// The live page: the tracker's answers as they are solved (rt_track.py PUSH, server-sent events), drawn on the scan.
//   ?data=<flight folder>   its index.json gives the scene and where the camera starts ("scene", "orbit"), the course line
//                           ("track": a race.json-like {"track": [[x, y, z], ...]}: the dashed line, and the orbit turns round its
//                           middle), and for a scanned room the room ("room", occlude.ts). <data>/countries.json ({name: ISO-2}) overrides the flags
//   ?feed=http://host:port  the tracker's PUSH port (default: this page's host, :8765 - right when rt_control.py serves it at /view/)
//   ?stream=<name>          only this cell of the feed (rt_track.py's output name, e.g. F1); default every cell, each a pilot
// The look follows the MGP page (viewer/mgp.html, src/mgp.ts, on the branch reconstruct-mgp-qualifier-tracks): "as mgp.ts" below points there.
//   ?cam=<regex>            the capture device shown as the live image (its label; default the Guermok USB capture)
//   ?replay=<name>          a recorded flight instead of the feed: <data>/rec/<name>.json (the feed's rows, as an array)
//                           and <name>.mp4 if it is there (what the sender sent). Each answer shows up when it did live
//                           (its frame's time plus the tracker's latency), so the sliders can be tried on the same flight.
//   ?from=30&to=175         the part of the recording that is played (seconds); it loops
//   ?ndi=1                  the picture rt_ndiview.py sends out (NDI or SRT): no panel and no capture box, and the camera is
//                           the control page's (rt_control.py /api/cam/events: each change pushed as it is made)
//   ?fps=30                 draw at most this often (rt_ndiview.py FPS): less GPU, splat sorting and screencast
//   ?mylaps=<url>           the lap timing decoder's passes (tools/dvr/rt_mylaps.py; default this page's host, :8766/passings) for the lap timeline
// The sliders and the camera (its mode, the Free view, the turn / tilt / zoom of the others) are remembered in this browser
// (localStorage), so a reload - OBS refreshing its source - comes back as it was; ?ndi=1 takes the control page's camera instead.
// The dot and the trail are live.ts': the dot 100 ms late, fitted between the answers around it; the trail refitted as
// answers arrive (the dot's delay is the slider's, 100 ms to start with). The clock is the tracker's (when each frame reached it), and "now" is the newest time on it that an
// answer could have arrived for, taken from the fastest recent answer (so a slow answer shows as lag, not as time).
import * as pc from 'playcanvas'
import { loadHQ, pickScan, saveHQ, sceneBlob } from './aud/scan'
import { LiveDraw, type Answer } from './live'
import { RoomOcclusion } from './occlude'

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const QS = new URLSearchParams(location.search)
const DATA = import.meta.env.BASE_URL + (/^[\w.-]+$/.test(QS.get('data') ?? '') ? QS.get('data') : 'data') + '/'   // a folder name only: never another site
const FEED = QS.get('feed') ?? `http://${location.hostname}:8765`   // served by rt_control.py (/view/): its tracker's PUSH
const REPLAY = QS.get('replay')
const NDI = QS.has('ndi')
const FPS = Number(QS.get('fps')) || 0
const hold = { paused: false }                             // rt_ndiview.py sets it (window.ghostlineHold) while no one receives: no update, no draw
if (FPS > 0 || NDI) {                                      // before the app starts: every animation frame of the page waits its turn
  const raf = window.requestAnimationFrame.bind(window); let due = 0
  window.requestAnimationFrame = (cb: FrameRequestCallback) => raf(function wait(t) { if (hold.paused || t + 2 < due) { raf(wait); return } due = Math.max(due, t) + (FPS > 0 ? 1000 / FPS : 0); cb(t) })
  Object.assign(window, { ghostlineHold: hold })
}
const ONLY = QS.get('stream')                     // one cell (rt_track.py's output name) to draw; unset: every cell on the feed
const KEY = 'ghostline-live:' + (QS.get('data') ?? 'data')
const stored = (() => { try { return JSON.parse(localStorage.getItem(KEY) ?? '{}') } catch { return {} } })()
const store = (o: object) => { try { Object.assign(stored, o); localStorage.setItem(KEY, JSON.stringify(stored)) } catch { /* private window: nothing is kept */ } }

// the panel hides behind a small tab at the corner (H, or ?panel=0 for OBS); this browser remembers it
const panelEl = $('panel'), ptBtn = $('pt')
const showPanel = (on: boolean) => { panelEl.hidden = !on; ptBtn.textContent = on ? '‹' : '›' }
showPanel(QS.has('panel') ? QS.get('panel') !== '0' : stored.panel !== false)
ptBtn.onclick = () => { showPanel(panelEl.hidden); store({ panel: !panelEl.hidden }); ptBtn.blur() }
window.addEventListener('keydown', e => { if ((e.key === 'h' || e.key === 'H') && !['INPUT', 'SELECT'].includes((e.target as HTMLElement).tagName)) ptBtn.click() })

const canvas = $<HTMLCanvasElement>('c'), stateEl = $('state'), info = $('info')
const app = new pc.Application(canvas, { mouse: new pc.Mouse(canvas), graphicsDeviceOptions: { antialias: true } })   // MSAA: the trail fades by alpha-to-coverage, as mgp.ts
// the canvas fills what the timeline below leaves of the window (live.html's column), not the window
app.setCanvasFillMode(pc.FILLMODE_NONE); app.setCanvasResolution(pc.RESOLUTION_AUTO)
new ResizeObserver(() => { app.resizeCanvas(canvas.clientWidth, canvas.clientHeight); canvas.style.width = canvas.style.height = '' }).observe(canvas)   // resizeCanvas writes the size into the style: cleared, so the CSS keeps deciding
const cam = new pc.Entity('cam')
cam.addComponent('camera', { clearColor: new pc.Color(0.91, 0.92, 0.94), fov: 60, nearClip: 0.1, farClip: 500 })
app.root.addChild(cam)
// the text's size (the name tags, the lap timeline): the slider's, remembered, or ?ts= (OBS's browser source keeps no slider)
const tsIn = $<HTMLSelectElement>('ts')
const pickTs = (v: number) => {                          // a size not on the list (?ts=) joins it
  if (!(v > 0)) return; const k = String(v)
  if (!Array.from(tsIn.options).some(o => o.value === k)) tsIn.add(new Option(k + '×', k))
  tsIn.value = k
}
if (QS.has('ts')) pickTs(Number(QS.get('ts'))); else if (stored.ts) pickTs(Number(stored.ts))
const showTs = () => document.documentElement.style.setProperty('--ts', tsIn.value); showTs()
tsIn.onchange = () => { showTs(); store({ ts: Number(tsIn.value) }); tsIn.blur() }
// the vertical field of view: the slider's, remembered; the orbit's fit and the trail's width follow it
const fovIn = $<HTMLInputElement>('fov'); if (stored.fov) fovIn.value = String(stored.fov)
const showFov = () => { $('fovv').textContent = fovIn.value.padStart(3) + '°'; cam.camera!.fov = Number(fovIn.value) }; showFov()
fovIn.oninput = () => { showFov(); store({ fov: Number(fovIn.value) }) }
const halfFov = () => Number(fovIn.value) * Math.PI / 360

// --- Free mode: drag orbits, shift or right drag pans, the wheel dollies (as the viewer's). The other modes: drag turns and tilts, the wheel zooms
const orbit = { target: new pc.Vec3(0, 1, 0), yaw: 0, pitch: -45, dist: 12 }, tmpV = new pc.Vec3()
let drag: { x: number; y: number; btn: number } | null = null
canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, btn: e.button }; canvas.setPointerCapture(e.pointerId) })
const keepView = () => { if (!NDI) store({ orbit: { target: [orbit.target.x, orbit.target.y, orbit.target.z], dist: orbit.dist, yaw: orbit.yaw, pitch: orbit.pitch }, view: { ...view }, cam: camMode }) }
const setView = (o: any) => { if (o.target) orbit.target.set(o.target[0], o.target[1], o.target[2]); orbit.dist = o.dist ?? orbit.dist; orbit.yaw = o.yaw ?? orbit.yaw; orbit.pitch = o.pitch ?? orbit.pitch }
canvas.addEventListener('pointerup', () => { drag = null; keepView() })
canvas.addEventListener('contextmenu', e => e.preventDefault())
canvas.addEventListener('pointermove', e => {
  if (!drag) return
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY
  if (camMode === 'top') { view.topYaw -= dx * 0.3; return }
  if (camMode !== 'free') { view.yawOffset -= dx * 0.3; view.pitch = Math.max(-85, Math.min(-5, view.pitch - dy * 0.3)); return }
  if (drag.btn === 0 && !e.shiftKey) { orbit.yaw -= dx * 0.3; orbit.pitch = Math.max(-89, Math.min(89, orbit.pitch - dy * 0.3)) }
  else {
    const r = new pc.Quat().setFromEulerAngles(orbit.pitch, orbit.yaw, 0), k = orbit.dist * 0.0015
    orbit.target.sub(r.transformVector(new pc.Vec3(dx * k, 0, 0), tmpV)).add(r.transformVector(new pc.Vec3(0, dy * k, 0), tmpV))
  }
})
canvas.addEventListener('wheel', e => {
  if (camMode === 'free') { orbit.dist = Math.max(0.5, orbit.dist * Math.exp(e.deltaY * 0.001)); keepView() }
  else { view.zoom = Math.max(0.4, Math.min(3, view.zoom * Math.exp(e.deltaY * 0.001))); keepView() }
  e.preventDefault()
}, { passive: false })

// --- the camera, as mgp.ts: orbit (the default: turns slowly round the course's middle - or, with no course line, the
// pilots in the air - as near as holds the course and them all),
// top, follow one pilot, and free (the drag / wheel orbit above). Keys 1-3, then 4+ for the pilots; a switch glides.
// mgp.ts plans its orbit over the whole recorded round; live there is no future, so the same fit (everyone inside 85% of
// the frame, at least 12 m off) is chased: closing in over 2.5 s, backing off over 1 s, the look-at over 1.5 s.
const PITCH0 = -18, TURN = 6, BLEND = 0.7
const view = { yawOffset: 30, topYaw: 0, pitch: PITCH0, zoom: 1 }
let camMode = 'orbit', blendT = BLEND
if (!NDI && typeof stored.cam === 'string') { camMode = stored.cam; Object.assign(view, stored.view ?? {}) }   // as this browser left it (a pilot not on the feed yet: orbit until it is)
const blendFrom = { pos: new pc.Vec3(), rot: new pc.Quat() }
const home = { target: new pc.Vec3(0, 0, 0), dist: 70 }    // the course (index.json orbit): where the orbit rests with no one up
let line: pc.Vec3[] = [], lineS: number[] = []              // the course line in full, and each point's arc length (the dashes)
let course: pc.Vec3[] = []                                 // the course line (index.json track), thinned: the orbit turns round its middle and holds all of it
const orb = { target: new pc.Vec3(), dist: 70, init: false }
function enterFree() {                                     // free starts where the camera is
  const fwd = cam.forward.clone(), eye = cam.getPosition(), d = orb.dist
  orbit.target.copy(eye).add(fwd.clone().mulScalar(d)); orbit.dist = d
  orbit.yaw = Math.atan2(-fwd.x, -fwd.z) * 180 / Math.PI; orbit.pitch = Math.max(-89, Math.min(89, Math.asin(Math.max(-1, Math.min(1, fwd.y))) * 180 / Math.PI))
}
function setMode(v: string) {
  if (v === camMode) return
  blendFrom.pos.copy(cam.getPosition()); blendFrom.rot.copy(cam.getRotation()); blendT = 0
  if (v === 'free') enterFree()
  camMode = v; drawCamSeg(); keepView()
}
const camSeg = $('camseg'); let segKey = ''
function drawCamSeg() {                                    // Orbit / Top / Free, then a follow button per pilot heard
  const ps = inOrder(), key = camMode + '|' + ps.map(p => p.name + p.color + whoOf(p)).join(',')   // a colour and a name can change once names arrive
  if (key === segKey) return; segKey = key
  const btn = (v: string, label: string, color?: string) => { const b = document.createElement('button'); b.textContent = label; b.className = v === camMode ? 'on' : ''
    if (color) b.style.borderLeft = `6px solid ${color}`; b.onclick = () => { setMode(v === camMode && v[0] === 'p' ? 'orbit' : v); b.blur() }; return b }
  camSeg.replaceChildren(btn('orbit', 'Orbit'), btn('top', 'Top'), btn('free', 'Free'), ...ps.map(p => btn('p' + p.name, whoOf(p) ? `${p.name} ${whoOf(p)}` : p.name, p.color)))
}
window.addEventListener('keydown', e => {
  if ((e.target as HTMLElement).tagName === 'INPUT') return
  const k = Number(e.key); if (!(k >= 1)) return
  const ps = inOrder(), v = k <= 3 ? ['orbit', 'top', 'free'][k - 1] : ps[k - 4] ? 'p' + ps[k - 4].name : ''
  if (v) setMode(v)
})
if (NDI) {                                                // mode orbit | top | p<output name> (not on the feed yet: orbit until it is), zoom 0.4-3
  $('panel').style.display = 'none'
  const ev = new EventSource('/api/cam/events')            // the browser retries a dropped connection, but gives up on an HTTP error: load again
  ev.onmessage = e => { const c = JSON.parse(e.data); if (typeof c.zoom === 'number') view.zoom = c.zoom; if (typeof c.mode === 'string') setMode(c.mode) }
  ev.onerror = () => { if (ev.readyState === EventSource.CLOSED) setTimeout(() => location.reload(), 3000) }
}
let followOn = false, lostAt = 0                           // the followed pilot was on screen last frame; when it went
function cameraAt(t: number, dt: number) {
  const up = [...pilots.values()].filter(p => p.dot.enabled && p.state > 1).map(p => p.dot.getPosition())   // in the air and answered
  if (!orb.init) { orb.target.copy(home.target); orb.dist = home.dist; orb.init = true }
  if (camMode === 'free') {
    const r = new pc.Quat().setFromEulerAngles(orbit.pitch, orbit.yaw, 0)
    cam.setPosition(r.transformVector(new pc.Vec3(0, 0, orbit.dist), new pc.Vec3()).add(orbit.target)); cam.setRotation(r); return
  }
  if (camMode === 'top') {
    const r = new pc.Quat().setFromEulerAngles(-89.9, view.topYaw, 0)
    cam.setPosition(home.target.x, home.target.y + home.dist * 1.1 * view.zoom, home.target.z); cam.setRotation(r); return
  }
  if (camMode[0] === 'p') {
    const p = pilots.get(camMode.slice(1)), on = !!p?.dot.enabled, now = performance.now()
    if (on) lostAt = 0; else if (followOn && !lostAt) lostAt = now
    if (!on && lostAt && now - lostAt < 3000) return          // lost for a moment: hold where the camera is
    if (on !== followOn) { blendFrom.pos.copy(cam.getPosition()); blendFrom.rot.copy(cam.getRotation()); blendT = 0; followOn = on }   // the pilot came, or is gone: glide, not cut
    if (p && on) {                              // behind and above the drone, looking a little ahead along where it is going
      const now = p.dot.getPosition().clone(), fwd = p.fwd
      cam.setPosition(now.clone().sub(fwd.clone().mulScalar(8 * view.zoom)).add(new pc.Vec3(0, 3 * view.zoom, 0))); cam.lookAt(now.add(fwd.clone().mulScalar(5)))
      return
    }
  }
  const r = new pc.Quat().setFromEulerAngles(view.pitch, view.yawOffset + TURN * t, 0)
  const dir = r.transformVector(new pc.Vec3(0, 0, 1), new pc.Vec3()), right = r.transformVector(new pc.Vec3(1, 0, 0), new pc.Vec3()), upv = r.transformVector(new pc.Vec3(0, 1, 0), new pc.Vec3())
  const goal = course.length ? home.target : up.length ? up.reduce((a, b) => a.add(b), new pc.Vec3()).mulScalar(1 / up.length) : home.target
  orb.target.lerp(orb.target, goal, 1 - Math.exp(-dt / 1.5))
  const tanV = Math.tan(halfFov()) * 0.85, tanH = tanV * canvas.clientWidth / Math.max(1, canvas.clientHeight)
  let need = course.length || up.length ? 12 : home.dist
  for (const q of course.length ? course.concat(up) : up) { const v = q.clone().sub(orb.target), a = v.dot(right), b = v.dot(upv), c = v.dot(dir); need = Math.max(need, Math.abs(a) / tanH + c, Math.abs(b) / tanV + c) }
  need *= view.zoom; orb.dist += (need - orb.dist) * (1 - Math.exp(-dt / (need > orb.dist ? 1.0 : 2.5)))
  cam.setPosition(r.transformVector(new pc.Vec3(0, 0, orb.dist), new pc.Vec3()).add(orb.target)); cam.setRotation(r)
}

// --- scene
const fail = (what: string) => (e: unknown) => { console.error(what, e); stateEl.textContent = 'NO ' + what.toUpperCase(); stateEl.className = '' }
const json = (url: string) => fetch(url).then(r => { if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json() })
json(DATA + 'index.json').then(ix => {
  setView(stored.orbit ?? ix.orbit ?? {})               // the view this browser left, else the flight's own start
  if (ix.orbit?.target) { home.target.set(ix.orbit.target[0], ix.orbit.target[1], ix.orbit.target[2]); home.dist = ix.orbit.dist ?? home.dist; orb.init = false }
  if (ix.track) json(DATA + ix.track).then(j => {   // the course's middle (its bounds' centre, at its median height) becomes the orbit's
    const t: number[][] = j.track; if (!t?.length) return
    course = t.filter((_, k) => k % 8 === 0).map(q => new pc.Vec3(q[0], q[1], q[2]))
    line = t.map(q => new pc.Vec3(q[0], q[1], q[2])); lineS = [0]; for (let k = 1; k < line.length; k++) lineS.push(lineS[k - 1] + line[k].distance(line[k - 1]))
    const mid = (k: number) => { const v = t.map(q => q[k]); return (Math.min(...v) + Math.max(...v)) / 2 }, ys = t.map(q => q[1]).sort((a, b) => a - b)
    home.target.set(mid(0), ys[ys.length >> 1], mid(2)); orb.init = false
    if (j.gate) {                                   // the start gate (track.json's, as make_race.py's): the race's way through it is the course line's there
      const c = new pc.Vec3(j.gate.centre[0], j.gate.centre[1], j.gate.centre[2]), w = new pc.Vec3(j.gate.width_dir[0], 0, j.gate.width_dir[2]).normalize(), nv = new pc.Vec3(w.z, 0, -w.x)
      let k = 0; line.forEach((q, i) => { if (q.distance(c) < line[k].distance(c)) k = i }); const n = line.length
      gate = { c, w, n: nv, half: j.gate.half_width, dir: Math.sign(line[(k + 1) % n].clone().sub(line[(k - 1 + n) % n]).dot(nv)) }
    }
    // the course's other gates, in order (track.json "gates": centre, half_width, width_dir, dir - the way through, as the
    // plane's normal (width_dir.z, 0, -width_dir.x) - and margin, m past the posts: 0.5 where the way back passes close by)
    splitGates = (j.gates ?? []).map((g: any) => { const w = new pc.Vec3(g.width_dir[0], 0, g.width_dir[2]).normalize(); return { name: g.name, c: new pc.Vec3(g.centre[0], g.centre[1], g.centre[2]), w, n: new pc.Vec3(w.z, 0, -w.x), half: g.half_width + (g.margin ?? 1.5), dir: g.dir } })
    if (j.qual_laps) qualLaps = j.qual_laps          // a practice / qualifying run's bar: the most laps begun in 90 s, per class (track.json's)
    if (j.launch) launchArea ={ x: j.launch.centre[0], y: j.launch.centre[1], z: j.launch.centre[2], r: j.launch.radius }   // the start pads (track.json's)
  }).catch(e => console.error('course line', e))     // the page still works: no line, and the orbit turns round the pilots
  $('resetview').onclick = () => { setView(ix.orbit ?? {}); keepView(); Object.assign(view, { yawOffset: 30, topYaw: 0, pitch: PITCH0, zoom: 1 }); orb.init = false; setMode('orbit') }
  // the scan: lite unless HQ (aud/scan.ts, as the audience pages), through Cache Storage; a switch keeps the old one on
  // screen until the new one is in. HQ is shown only when the data has a lite scan to switch from
  let shown: { name: string; e: pc.Entity } | null = null, want = ''
  const hqIn = $<HTMLInputElement>('hq'); hqIn.parentElement!.hidden = !ix.lite; hqIn.checked = loadHQ()
  hqIn.onchange = () => { saveHQ(hqIn.checked); hqIn.blur(); show().catch(fail('scene')) }
  async function show() {
    const name = pickScan(ix, hqIn.checked); if (shown?.name === name) return; want = name
    const url = URL.createObjectURL(await sceneBlob(`${DATA}scene/${name}.sog`))
    const a = new pc.Asset(name, 'gsplat', { url, filename: `${name}.sog` }); app.assets.add(a)
    a.on('load', () => {
      URL.revokeObjectURL(url)
      if (want !== name) { app.assets.remove(a); a.unload(); return }            // switched again while this one came
      const e = new pc.Entity('scene'); e.addComponent('gsplat', { asset: a }); app.root.addChild(e)
      if (shown) { const o = shown.e.gsplat?.asset, oa = typeof o === 'number' ? app.assets.get(o) : o; shown.e.destroy(); if (oa) { app.assets.remove(oa); oa.unload() } }
      shown = { name, e }
      if (ix.room) {
        const box = $<HTMLInputElement>('seein'); box.parentElement!.hidden = false; box.checked = stored.seein !== false
        occ = new RoomOcclusion(e.gsplat!, ix.room); occ.enabled = box.checked
        box.onchange = () => { occ!.enabled = box.checked; store({ seein: box.checked }) }
      }
    }); a.on('error', fail('scene')); app.assets.load(a)
  }
  show().catch(fail('scene'))
}).catch(fail('index.json'))
let occ: RoomOcclusion | null = null                   // the scene entity sits at the origin: the camera's position is in its frame


// --- the pilots: one per cell of the feed (rt_track.py's "stream"), drawn as the MGP page draws them (mgp.ts): a small sphere,
// a trail in the pilot's colour that thins and fades to its old end, and a name tag. Each keeps its own answers and its own clock.
// the cell's colour - the venue's heat table's (JDL R7: R2 yellow, F1 green, R4 blue, R5 orange), a little more vivid - as the
// control page's, /obs and the audience pages' (rt_control.html, aud/look.css --tl ... --br): the
// cell each output fills comes from rt_control.py's /api/state; without it (another server, a replay), arrival order
const SLOT_HEX: Record<string, string> = { tl: '#e8a800', tr: '#22a849', bl: '#2f7bf0', br: '#f0701e' }, PALETTE = Object.values(SLOT_HEX)
const hex = (h: string) => new pc.Color(parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255)
const cHold = new pc.Color(0.85, 0.55, 0.05), cLost = new pc.Color(0.55, 0.57, 0.6)

// --- the trail: a band a few pixels wide that faces the camera and thins out towards its old end (WebGL lines are 1 px).
// As race.ts' ribbon, but broken wherever a frame has no position, and drawn with depth as mgp.ts': it is opaque, goes
// into the World layer's opaque pass (before the splats) and writes depth, so splats in front of it blend over it and
// the ones behind it are rejected - the trail passes behind gates and trees instead of lying on top of the picture.
const TRAIL_PX = 5
const trailMat = new pc.StandardMaterial()
{
  const m = trailMat; m.useLighting = false; m.diffuse = new pc.Color(0, 0, 0); m.cull = pc.CULLFACE_NONE
  m.emissiveVertexColor = true; m.emissive = new pc.Color(1, 1, 1); m.blendType = pc.BLEND_NONE; m.depthWrite = true; m.depthTest = true
  // the fade towards the old end is alpha-to-coverage, as mgp.ts (racekit ribbonMesh): a blended band drawn before the
  // splats would mix with the empty background and still hide the splats behind it; MSAA's stipple was preferred to the
  // blue-noise dither this page had, which shimmered
  m.opacityVertexColor = true; m.opacityVertexColorChannel = 'a'; m.alphaToCoverage = true; m.update()
}
function emptyTrail(m: pc.Mesh) { m.setPositions([0, 0, 0, 0, 0, 0, 0, 0, 0]); m.setNormals([0, 1, 0, 0, 1, 0, 0, 1, 0]); m.setColors(new Array(12).fill(0)); m.setIndices([0, 1, 2]); m.update(pc.PRIMITIVE_TRIANGLES) }
function trailMesh() {
  const m = new pc.Mesh(app.graphicsDevice); m.clear(true, false); emptyTrail(m)   // every stream from the start: the shader is built from the first mesh
  const e = new pc.Entity('trail'); e.addComponent('render', { meshInstances: [new pc.MeshInstance(m, trailMat)] }); app.root.addChild(e)
  return m
}
function drawTrail(mesh: pc.Mesh, pts: (number[] | null)[], c: pc.Color, eye: pc.Vec3, pxWorld: number) {
  const pos: number[] = [], col: number[] = [], nrm: number[] = [], idx: number[] = [], n = pts.length
  const tan = new pc.Vec3(), view = new pc.Vec3(), side = new pc.Vec3(), a = new pc.Vec3(), b = new pc.Vec3(), q = new pc.Vec3()
  let run = 0                                         // vertex pairs in the current unbroken run
  for (let k = 0; k < n; k++) {
    const p = pts[k]; if (!p) { run = 0; continue }
    const pa = pts[k - 1] ?? p, pb = pts[k + 1] ?? p
    q.set(p[0], p[1], p[2]); a.set(pa[0], pa[1], pa[2]); b.set(pb[0], pb[1], pb[2])
    tan.sub2(a, b); if (tan.lengthSq() < 1e-12) tan.set(1, 0, 0); tan.normalize(); view.sub2(eye, q)
    const al = k / Math.max(1, n - 1)                // 0 at the old end, 1 at the drone
    // as mgp.ts: TRAIL_PX at the drone, 1 px at the old end
    const w = (1 + (TRAIL_PX - 1) * al) / 2 * pxWorld * view.length(); side.cross(tan, view.normalize()); if (side.lengthSq() < 1e-12) side.set(0, 1, 0); side.normalize().mulScalar(w)
    pos.push(q.x + side.x, q.y + side.y, q.z + side.z, q.x - side.x, q.y - side.y, q.z - side.z); col.push(c.r, c.g, c.b, al, c.r, c.g, c.b, al); nrm.push(0, 1, 0, 0, 1, 0)
    if (run) { const i = pos.length / 3 - 2; idx.push(i - 2, i - 1, i, i - 1, i + 1, i) }
    run++
  }
  if (idx.length < 3) { emptyTrail(mesh); return }
  mesh.setPositions(pos); mesh.setNormals(nrm); mesh.setColors(col); mesh.setIndices(idx); mesh.update(pc.PRIMITIVE_TRIANGLES)
}

// --- the course line, as mgp.ts draws it: a white dashed band, 0.12 m wide wherever it is (1.0 m on, 0.7 m off), faint,
// blended over the splats without depth (the immediate layer)
const LINE_W = 0.12, LINE_DASH = 1.0, LINE_GAP = 0.7, LINE_A = 0.18
const lineMesh = new pc.Mesh(app.graphicsDevice); lineMesh.clear(true, false); emptyTrail(lineMesh)
{
  const m = new pc.StandardMaterial(); m.useLighting = false; m.diffuse = new pc.Color(0, 0, 0); m.cull = pc.CULLFACE_NONE
  m.emissiveVertexColor = true; m.emissive = new pc.Color(1, 1, 1); m.opacityVertexColor = true; m.opacityVertexColorChannel = 'a'
  m.blendType = pc.BLEND_NORMAL; m.depthWrite = false; m.depthTest = false; m.update()
  const e = new pc.Entity('course'); e.addComponent('render', { meshInstances: [new pc.MeshInstance(lineMesh, m)], layers: [pc.LAYERID_IMMEDIATE] }); app.root.addChild(e)
}
const lineIn = $<HTMLInputElement>('line'); if (stored.line === false) lineIn.checked = false
lineIn.onchange = () => store({ line: lineIn.checked })
function drawLine(eye: pc.Vec3) {                          // closed: the last point joins the first
  const n = line.length; if (!n || !lineIn.checked) { emptyTrail(lineMesh); return }
  const pos: number[] = [], col: number[] = [], nrm: number[] = [], idx: number[] = [], tan = new pc.Vec3(), view = new pc.Vec3(), side = new pc.Vec3()
  for (let k = 0; k < n; k++) {
    const q = line[k]; tan.sub2(line[(k - 1 + n) % n], line[(k + 1) % n]).normalize(); view.sub2(eye, q)
    side.cross(tan, view.normalize()).normalize().mulScalar(LINE_W / 2)
    const al = lineS[k] % (LINE_DASH + LINE_GAP) < LINE_DASH ? LINE_A : 0
    pos.push(q.x + side.x, q.y + side.y, q.z + side.z, q.x - side.x, q.y - side.y, q.z - side.z); col.push(1, 1, 1, al, 1, 1, 1, al); nrm.push(0, 1, 0, 0, 1, 0)
    const i = 2 * k, j = 2 * ((k + 1) % n); idx.push(i, i + 1, j, i + 1, j + 1, j)
  }
  lineMesh.setPositions(pos); lineMesh.setNormals(nrm); lineMesh.setColors(col); lineMesh.setIndices(idx); lineMesh.update(pc.PRIMITIVE_TRIANGLES)
}

// --- the tracker's revisions (rt_track.py BA): with every answer it sends the poses of its BA window solved again
// together ("win": [frame, x, y, z, qx, qy, qz, qw]). An answer is drawn at the newest revision that has arrived; the raw
// answer is kept so the box can be unticked to compare.
type Stored = Answer & { raw: { pos: number[]; quat: number[] }; src: number }
const baIn = $<HTMLInputElement>('ba'); if (stored.ba === false) baIn.checked = false
class Pilot {
  live: LiveDraw | null = null; fps = 60; offset = Infinity; tsLive = -Infinity; lastTau = 0; lastMsg = 0; lastLat = 0; lastInl = 0
  laps: number[][] = []; seen: number[][] = []; gs: number | null = null; pad: number[][] = []; launch: number | null = null; hs: number | null = null; leftPad = -Infinity; splits: number[][] = []; gss: (number | null)[] = []; arc: number | null = null; lap = 0; done = 0; lap1: number | null = null   // lap1: when it first went round past the start (the holeshot when no start off the pads was seen); arc: where on the course line (m from the start gate); lap: times round past the start; done: the race's laps when it finished   // [time, gate] through the other gates; each gate's side last frame   // [time, on the launch area] of the last 30 s; when it left; its holeshot   // [time, lap or NaN] at each gate crossing; [from, to] on screen; the side of the gate last frame
  base = 0; trailFrom = 0; resmooth = true; state = 0; lift = 0; order = 0; fwd = new pc.Vec3(0, 0, -1); prev: pc.Vec3 | null = null   // state: 1 lost, 2 hold, 3 tracking; fwd: the level heading, for follow
  offs: number[][] = []; got: number[] = []                     // (local, local - tau) of recent answers; arrival times, for the rate
  byFrame = new Map<number, Stored>()                            // the tracker's frame number -> the answer
  mat = new pc.StandardMaterial(); dot = new pc.Entity('dot'); trail = trailMesh(); tag = document.createElement('div')
  // the colour is its cell's (SLOT_HEX), so every page and every viewer shows the same; arrival order without it
  get color() { return SLOT_HEX[slotOf[this.name]] ?? PALETTE[this.seq % PALETTE.length] }
  private colOf = ['', new pc.Color()]
  get col() { const c = this.color; if (c !== this.colOf[0]) this.colOf = [c, hex(c)]; return this.colOf[1] as pc.Color }
  constructor(public name: string, public seq: number) {
    const m = this.mat; m.useLighting = false; m.diffuse = new pc.Color(0, 0, 0); m.emissive = this.col; m.depthWrite = true; m.depthTest = true; m.update()
    this.dot.addComponent('render', { type: 'sphere', material: m, castShadows: false })   // opaque with depth, as mgp.ts': behind what stands in front of it
    this.dot.setLocalScale(0.12, 0.12, 0.12); this.dot.enabled = false; app.root.addChild(this.dot)
    this.tag.className = 'tag'; this.tag.style.display = 'none'; document.body.appendChild(this.tag)
  }
  reset() { this.live = null; this.offset = Infinity; this.tsLive = -Infinity; this.got.length = 0; this.offs.length = 0; this.base = 0; this.trailFrom = 0; this.byFrame.clear() }
  revise(win: number[][] | undefined) { if (win && baIn.checked) for (const w of win) { const a = this.byFrame.get(w[0]); if (a) { a.pos = w.slice(1, 4); a.quat = w.slice(4, 8) } } }
  toRaw() { for (const a of this.byFrame.values()) { a.pos = a.raw.pos; a.quat = a.raw.quat } }
  clear() { this.byFrame.clear(); if (this.live) { this.base = this.live.byDone.length ? this.live.byDone[this.live.byDone.length - 1].i : 0; this.live = new LiveDraw([], this.fps); this.live.taper = true; this.resmooth = true } }
  hide() { this.state = 0; this.lift = 0; this.gs = null; this.dot.enabled = false; this.tag.style.display = 'none'; emptyTrail(this.trail) }
}
const pilots = new Map<string, Pilot>()
// who flies each cell: rt_control.py's /api/pilots (the venue's heat manager), every 5 s; nothing when it is not there
let names: { cells?: Record<string, string>; heat?: string; cls?: string; stale?: boolean } = {}
let namesErr = ''
const pollNames = () => fetch('/api/pilots').then(r => { if (r.status === 404) { clearInterval(namesTimer); throw new Error('no /api/pilots here') } if (!r.ok) throw new Error(String(r.status)); return r.json() })
  .then(j => { names = j ?? {}; namesErr = '' }).catch(e => { if (String(e) !== namesErr) console.warn('pilot names:', e); namesErr = String(e); names = { ...names, stale: true } })   // keep the last names: colours stay put
const namesTimer = REPLAY ? 0 : setInterval(pollNames, 5000); if (!REPLAY) pollNames()
let slotOf: Record<string, string> = {}                     // output name -> its cell (tl, tr, bl, br), from /api/state
const pollSlots = () => fetch('/api/state').then(r => r.ok ? r.json() : null).then(j => { if (j?.cells) slotOf = Object.fromEntries(Object.entries(j.cells as Record<string, { cell: string }>).map(([n, c]) => [n, c.cell])); followView(j?.view) }).catch(() => {})
if (!REPLAY) { pollSlots(); setInterval(pollSlots, 10000) }
// a flag beside each name, as mgp.ts': the heat manager gives names only, so the country is guessed from the name - kana or
// kanji: JP; a Korean family name among the romanized words: KR - unless <data>/countries.json ({name: ISO-2}) says
// (not the ones that are also romanized Japanese or English: yu, ko, go, jin, han, jo, an, min, son, ha, na, ji, im)
const KR = new Set('kim lee yi park choi jung jeong kang cho yoon yun jang lim oh seo shin kwon hwang ahn song yoo hong jeon moon yang bae baek heo nam noh kwak sung cha joo woo ryu uhm chae won byun bang yeo pyo myung'.split(' '))
let countries: Record<string, string> = {}
fetch(DATA + 'countries.json').then(r => r.ok ? r.json() : {}).then(j => { countries = j ?? {} }).catch(e => console.warn('countries.json', e))   // optional
const country = (name: string) => (Object.hasOwn(countries, name) ? countries[name] : null) ?? (/[\u3040-\u30ff\u4e00-\u9fff]/.test(name) ? 'JP' : name.toLowerCase().split(/[\s-]+/).some(w => KR.has(w)) ? 'KR' : '')
const flagOf = (cc: string) => /^[A-Z]{2}$/.test(cc) ? String.fromCodePoint(...[...cc].map(c => 0x1f1e6 + c.charCodeAt(0) - 65)) : ''
// every list of pilots - the follow buttons, the keys, the timeline's rows, the panel - in the cells' order (tl tr bl br: R2 F1 R4 R5
// at JDL), not as they first came; a cell the control page has not named yet goes after, in arrival order
const SLOT_ORDER = ['tl', 'tr', 'bl', 'br']
const inOrder = () => [...pilots.values()].sort((a, b) => ((SLOT_ORDER.indexOf(slotOf[a.name]) + 5) % 5 - (SLOT_ORDER.indexOf(slotOf[b.name]) + 5) % 5) || a.seq - b.seq)
const whoOf = (p: Pilot) => names.cells && Object.hasOwn(names.cells, p.name) ? String(names.cells[p.name]) : ''
const pilot = (name: string) => { let p = pilots.get(name); if (!p) { p = new Pilot(name, pilots.size); pilots.set(name, p) } return p }
const resmoothAll = () => { for (const p of pilots.values()) p.resmooth = true }   // fit the visible trails again from the answers

const dur = $<HTMLInputElement>('dur'); if (stored.trail) dur.value = String(stored.trail)
const showDur = () => { $('durv').textContent = Number(dur.value).toFixed(1).padStart(4) + ' s' }; showDur()
dur.oninput = () => { showDur(); store({ trail: Number(dur.value) }); resmoothAll() }   // a longer trail reaches back past what was kept
// smoothing: σ (ms) of the Gaussian mean every page draws with (live.ts gauss, the control page's top view's)
const wsIn = $<HTMLInputElement>('ws'); if (stored.sigma) wsIn.value = String(stored.sigma)
const showWs = () => { $('wsv').textContent = wsIn.value.padStart(3) + ' ms' }; showWs()
wsIn.oninput = () => { showWs(); store({ sigma: Number(wsIn.value) }); resmoothAll() }
// delay: how far behind real time the dot and the trail's head are drawn (live.ts 'delay'). The mean at the head only has
// the answers that have arrived: with a delay shorter than the mean's reach it leans on the past side and the head wobbles
// before it settles; a longer delay steadies it and the dot lags by that much.
const delayIn = $<HTMLInputElement>('delay'); if (stored.delayMs !== undefined) delayIn.value = String(stored.delayMs)
const showDelay = () => { $('delayv').textContent = delayIn.value.padStart(3) + ' ms' }; showDelay()
delayIn.oninput = () => { showDelay(); store({ delayMs: Number(delayIn.value) }) }
// served by the control page (rt_control.py), both follow its sliders (/api/state "view"): one look on every venue screen.
// A change made here holds until the control page's next change
let serverView = ''
function followView(v: { smooth?: number; delay?: number } | undefined) {
  if (!v || typeof v.smooth !== 'number' || typeof v.delay !== 'number') return
  const k = `${v.smooth}|${v.delay}`; if (k === serverView) return
  serverView = k; wsIn.value = String(v.smooth); delayIn.value = String(v.delay); showWs(); showDelay(); resmoothAll()
}

// --- the live image: the capture device itself, read by the browser (it is on this machine; the tracker gets the same
// frames over SRT or NDI). Raw and undelayed, so it runs ahead of the dot by the tracker's latency plus the dot's delay.
async function startVideo() {
  const video = $<HTMLVideoElement>('video'), note = $('dvrinfo'), want = new RegExp(QS.get('cam') ?? 'Guermok|USB3 Video', 'i')
  try {
    const find = async () => (await navigator.mediaDevices.enumerateDevices()).find(d => d.kind === 'videoinput' && want.test(d.label))
    let dev = await find()
    if (!dev) { (await navigator.mediaDevices.getUserMedia({ video: true })).getTracks().forEach(t => t.stop()); dev = await find() }   // labels are empty until the page has camera permission
    if (!dev) { note.textContent = 'capture device not found'; return }
    const s = await navigator.mediaDevices.getUserMedia({ video: { deviceId: { exact: dev.deviceId }, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 60 } } })
    video.srcObject = s; await video.play()
    const st = s.getVideoTracks()[0].getSettings(); note.textContent = `${st.width}x${st.height} ${Math.round(st.frameRate ?? 0)} fps`
  } catch (e) { note.textContent = 'no live image: ' + (e as Error).message }
}
if (!REPLAY && !NDI && navigator.mediaDevices) startVideo()
else if (!REPLAY) $('dvr').hidden = true               // plain http from another machine (rt_control.py's /view/), or ?ndi=1: no capture box

let events: { due: number; win: number[][] }[] = [], evK = 0, lastRt = 0   // replay: the revisions, in the order they came
baIn.onchange = () => { store({ ba: baIn.checked }); for (const p of pilots.values()) p.toRaw(); evK = 0; lastRt = 0; resmoothAll() }   // live: revisions start again with the next answer
$('clear').onclick = () => { for (const p of pilots.values()) p.clear() }

// --- the feed
const WEAK = 60, JUMP = 5                                  // inliers, m: see the jumps below
const nowS = () => performance.now() / 1000
function connect() {
  const es = new EventSource(FEED)
  es.onopen = () => { for (const p of pilots.values()) p.reset(); stateEl.textContent = 'WAITING'; stateEl.className = 'hold' }
  es.onerror = () => {                               // EventSource retries a dropped connection itself, but not a refused or failed one
    stateEl.textContent = 'NO FEED'; stateEl.className = ''
    if (es.readyState === EventSource.CLOSED) setTimeout(connect, 3000)
  }
  es.onmessage = ev => {
    // The stream's clock is the tracker's: an answer's frame arrived there at done - lat. Counting frames instead
    // (i / fps) runs slow against real time - the capture delivers 59.2 fps, not 60 - and the page's "now" then creeps
    // ahead of the newest answer until everything reads as lost. Frames are numbered on that clock (tau * fps).
    const m = JSON.parse(ev.data), name = m.stream ?? 'feed'
    if (ONLY && name !== ONLY) return
    const p = pilot(name), t = nowS(); p.lastMsg = t; p.fps = m.fps || p.fps
    const tau = m.done - (m.lat ?? 0) / 1000, fi = Math.round(tau * p.fps)
    if (tau < p.lastTau - 1) p.reset()                // the tracker started again
    p.lastTau = tau
    if (!p.live) { p.live = new LiveDraw([], p.fps); p.live.taper = true; p.resmooth = true }
    // "now" on the stream's clock: local time minus the smallest (local - tau) of the last few seconds, i.e. the fastest
    // recent answer. A window, not an all-time minimum, so one burst after a stall does not shift the clock for good.
    p.offs.push([t, t - tau]); while (p.offs[0][0] < t - 5) p.offs.shift()
    p.offset = Math.min(...p.offs.map(o => o[1]))
    if (m.how === 'none') return
    const live = p.live, lastA = live.byDone[live.byDone.length - 1]
    // the tracker's jumps (JDL R7 2026-10-24: about 2 a pilot a heat, nearly all on 30-60 inliers while tracking): a weak
    // answer far from the one before is not drawn; a strong one is believed wherever it lands (rt_relay.py does the same)
    if ((m.inl ?? 0) < WEAK && lastA && fi - lastA.i < 0.5 * p.fps && Math.hypot(m.pos[0] - lastA.pos[0], m.pos[1] - lastA.pos[1], m.pos[2] - lastA.pos[2]) > JUMP) return
    while (live.byDone.length && live.byDone[0].i < fi - 70 * p.fps) p.byFrame.delete((live.byDone.shift() as Stored).src)   // older than any trail the slider allows
    const prev = live.byDone.length ? live.byDone[live.byDone.length - 1].done : -Infinity   // byDone is searched by done: keep it in order when the offset grows
    const a = { i: fi, done: Math.max(t - p.offset, prev), pos: m.pos, quat: m.quat, inl: m.inl, how: m.how, raw: { pos: m.pos, quat: m.quat }, src: m.i } as Stored
    live.byDone.push(a); p.byFrame.set(m.i, a); p.revise(m.win)
    p.got.push(t); while (p.got[0] < t - 2) p.got.shift()   // also here: with the page held (rt_ndiview.py) the draw loop does not trim it
    p.lastLat = m.lat ?? 0; p.lastInl = m.inl
  }
}
// --- replay: the recording's own clock (the video's when there is one), every answer due at frame time + latency
let rt = 0, rEnd = 0, rFrom = 0, rTo = 0, playing = true, hasVideo = false, due: number[] = [], rp: Pilot | null = null
const video = $<HTMLVideoElement>('video'), seek = $<HTMLInputElement>('seek')
async function loadReplay(name: string) {
  const rows: any[] = await fetch(`${DATA}rec/${name}.json`).then(r => r.json())
  const p = rp = pilot(name)
  // the capture's real rate (59.2, not the nominal 60): frames counted against the tracker's clock
  const a = rows[0], b = rows[rows.length - 1], tau = (m: any) => m.done - (m.lat ?? 0) / 1000
  p.fps = (b.i - a.i) / (tau(b) - tau(a)); rEnd = (b.i - a.i) / p.fps
  // frames from the recording's first row (a recording made with the video starts at the tracker's frame 0 anyway)
  const ans = rows.filter(m => m.how !== 'none').map(m => ({ i: m.i - a.i, done: (m.i - a.i) / p.fps + (m.lat ?? 0) / 1000, pos: m.pos, quat: m.quat, inl: m.inl, how: m.how, lat: m.lat,
    raw: { pos: m.pos, quat: m.quat }, src: m.i }))
  for (const x of ans) p.byFrame.set(x.src, x as unknown as Stored)
  events = rows.filter(m => m.win).map(m => ({ due: (m.i - a.i) / p.fps + (m.lat ?? 0) / 1000, win: m.win as number[][] })).sort((x, y) => x.due - y.due)
  baIn.parentElement!.hidden = !events.length          // a recording made without BA has nothing to switch
  p.live = new LiveDraw(ans as Answer[], p.fps); p.live.taper = true; due = p.live.byDone.map(x => x.done)
  rFrom = Math.min(rEnd, Number(QS.get('from') ?? Math.max(0, (ans[0]?.i ?? 0) / p.fps - 1))); rTo = Math.min(rEnd, Number(QS.get('to') ?? rEnd))
  rt = rFrom; seek.min = String(QS.has('from') ? rFrom : 0); seek.max = String(rTo); $('replaybar').hidden = false
  video.src = `${DATA}rec/${name}.mp4`
  video.onloadedmetadata = () => { hasVideo = true; video.currentTime = rt; video.play(); $('dvrinfo').textContent = 'recorded' }
  video.onerror = () => { $('dvrinfo').textContent = 'no video in this recording' }
  jump = (v: number) => { rt = v; if (hasVideo) { video.currentTime = v; if (playing) video.play() } p.resmooth = true }
  seek.oninput = () => jump(Number(seek.value))
  $('play').onclick = () => { playing = !playing; $('play').textContent = playing ? 'pause' : 'play'; if (hasVideo) { if (playing) video.play(); else video.pause() } }
  window.addEventListener('keydown', e => { if (e.key === ' ') { $('play').click(); e.preventDefault() } if (e.key === 'ArrowLeft') jump(Math.max(Number(seek.min), rt - 5)); if (e.key === 'ArrowRight') jump(Math.min(rTo, rt + 5)) })
}
let jump = (_: number) => {}
const upTo = (v: number) => { let lo = 0, hi = due.length; while (lo < hi) { const m = (lo + hi) >> 1; if (due[m] <= v) lo = m + 1; else hi = m } return lo }   // answers due by v
if (REPLAY) loadReplay(REPLAY).catch(() => { stateEl.textContent = 'NO RECORDING'; stateEl.className = '' }); else connect()

// --- the lap timeline, as mgp.ts': a row per pilot from the heat's first start off the launch pads (a practice or
// qualifying run is 90 s from each pilot's own first pass through the start gate: a dashed line in its row marks the end, and the axis runs on to show the lap that finishes after it), a line at each
// pass with the lap's number and time beside it, and a playhead at now. A heat opens when a pilot shows up after 20 s with
// no one (the last heat's rows go then); its rows stay after it until the next one.
// The passes are the venue's lap timing decoder's where it is heard (tools/dvr/rt_mylaps.py on the tracker's box): each
// transponder is matched to the pilot whose own crossing (below) comes within 1.5 s of its passes, afresh each heat. A pilot
// with no transponder matched keeps its own crossings: the drawn dot (delayed and smoothed, as everyone sees it) going
// through the gate's vertical plane the race's way, within 1.5 m of its posts and below 5 m; one per 3 s at most.
const RACE = 90, TL = 105, LAP_MAX = 90, QUIET = 20, MATCH = 1.5, CLEAR = 0.5   // the run, the axis (s); a longer gap counts from a holeshot again; no one this long: a new heat; a pass and a crossing this close are one
let gate: { c: pc.Vec3; w: pc.Vec3; n: pc.Vec3; half: number; dir: number } | null = null
let launchArea: { x: number; y: number; z: number; r: number } | null = null
let splitGates: { name: string; c: pc.Vec3; w: pc.Vec3; n: pc.Vec3; half: number; dir: number }[] = []
let qualLaps: Record<string, number> = {}
const heat = { open: null as number | null, last: -Infinity, hs0: null as number | null, nkey: '', nchanged: -Infinity }   // hs0: its first holeshot; nkey / nchanged: the heat manager's heat and class, and when they last changed
const tmpG = new pc.Vec3()
// a race's progress (the "4 laps" run): where a drone is along the course line, as metres from the start gate. The line
// crosses itself (the north loops, the netting in the middle), so it is looked for from a little behind the last place to
// 40 m on, and over all of it only when that finds nothing within 10 m. Going round past the line's end is a lap; the
// first one is the holeshot (the pads are off the end of the lap)
function advance(p: Pilot, pos: number[], state: number, tc: number) {
  const n = line.length; if (n < 2 || state < 2) return
  const L = lineS[n - 1] + line[n - 1].distance(line[0]), near = (k: number) => Math.hypot(line[k].x - pos[0], line[k].z - pos[2])
  let best = -1, bd = Infinity
  if (p.arc !== null) for (let k = 0; k < n; k++) {
    let ds = lineS[k] - p.arc; if (ds < -L / 2) ds += L; if (ds > L / 2) ds -= L
    if (ds < -5 || ds > 40) continue; const d = near(k); if (d < bd) { bd = d; best = k }
  }
  if (best < 0 || bd > 10) { bd = Infinity; for (let k = 0; k < n; k++) { const d = near(k); if (d < bd) { bd = d; best = k } } }
  const sNow = lineS[best]
  if (p.arc !== null && p.arc > 0.7 * L && sNow < 0.3 * L) { p.lap++; if (p.lap1 === null) p.lap1 = tc }   // round past the start
  else if (p.arc !== null && p.arc < 0.3 * L && sNow > 0.7 * L && p.lap > 0) p.lap--  // back over it
  p.arc = sNow
}
const progressOf = (p: Pilot) => {                       // laps run since the holeshot, with the part of the one under way
  const n = line.length; if (n < 2 || p.arc === null || p.lap === 0) return 0
  const L = lineS[n - 1] + line[n - 1].distance(line[0]); return p.lap - 1 + p.arc / L
}
function crossing(p: Pilot, pos: number[], state: number, tc: number) {
  if (!gate || state < 2) { p.gs = null; return }            // a lost pilot's dot holds still: no crossing from it
  const q = tmpG.set(pos[0], pos[1], pos[2]).sub(gate.c), s = q.dot(gate.n), was = p.gs; p.gs = s
  if (was === null || Math.sign(s) === Math.sign(was) || (gate.dir && Math.sign(s - was) !== gate.dir)) return
  if (Math.abs(q.dot(gate.w)) > gate.half + 1.5 || q.y > 5) return
  const last = p.laps[p.laps.length - 1]; if (last && tc - last[0] < 3) return
  p.laps.push([tc, last && tc - last[0] < LAP_MAX ? tc - last[0] : NaN])
}
// the decoder's passes on this page's clock: rt_mylaps.py gives them on its box's clock with that clock's now, so the offset
// is taken at each answer (half the round trip off)
const MIN_HITS = 10
const MYLAPS = QS.get('mylaps') ?? `http://${location.hostname}:8766/passings`
let passes: { n: number; tx: number; tc: number; rtc: number }[] = []; const passSeen = new Set<number>()   // tc places a pass; rtc (the decoder's own clock, ms) times the laps
const pollPasses = async () => {
  const a = nowS(), j = await fetch(MYLAPS, { cache: 'no-store' }).then(r => r.ok ? r.json() : null).catch(() => null), b = nowS()
  if (!j?.passings) return
  const off = (a + b) / 2 - j.now / 1000
  // a read of a few hits is a transponder brushing past the loop, not through it (JDL R7: 3 hits at strength 83, where a
  // pass has 150-580 at 150 or more; the official result leaves it out)
  for (const x of j.passings) if (!passSeen.has(x.n)) { passSeen.add(x.n); if ((x.hits ?? 99) >= MIN_HITS) passes.push({ n: x.n, tx: x.tx, tc: x.t / 1000 + off, rtc: x.rtc }) }
}
if (!REPLAY) { pollPasses(); setInterval(pollPasses, 1000) }
function matchPasses(ps: Pilot[]) {                          // transponder -> pilot: the most passes with that pilot's crossing alone near
  const votes = new Map<number, Map<Pilot, number>>()
  for (const x of passes) {
    // the nearest pilot's crossing, when the next is CLEAR s farther: a close race crosses within a second (JDL R7 10/25 h19)
    const d = ps.map(p => [p, Math.min(Infinity, ...p.laps.map(l => Math.abs(l[0] - x.tc)))] as [Pilot, number]).sort((a, b) => a[1] - b[1])
    if (!d.length || d[0][1] >= MATCH || (d.length > 1 && d[1][1] - d[0][1] < CLEAR)) continue
    const v = votes.get(x.tx) ?? new Map(); v.set(d[0][0], (v.get(d[0][0]) ?? 0) + 1); votes.set(x.tx, v)
  }
  const pairs = [...votes].flatMap(([tx, v]) => [...v].map(([p, n]) => ({ tx, p, n }))).sort((a, b) => b.n - a.n)
  const of = new Map<Pilot, number>(), used = new Set<number>()
  for (const { tx, p } of pairs) if (!of.has(p) && !used.has(tx)) { of.set(p, tx); used.add(tx) }
  return of
}
const MIN_LAP = 8                                          // s: a pass sooner than this after the last is the same one again (a crash at the gate, a hop back through it; JDL R7's fastest lap is 12.5)
const lapsOf = (all: { tc: number; rtc: number }[]) => {
  const xs: typeof all = []; for (const x of all) if (!xs.length || x.rtc - xs[xs.length - 1].rtc >= MIN_LAP * 1000) xs.push(x)
  return xs.map((x, k) => { const d = k ? (x.rtc - xs[k - 1].rtc) / 1000 : NaN; return [x.tc, d < LAP_MAX ? d : NaN] })   // NaN < x is false: the first stays NaN
}
const tlBox = $('timeline'), tlC = $<HTMLCanvasElement>('tl'), lapsIn = $<HTMLInputElement>('laps'); if (stored.laps === false) lapsIn.checked = false
// the run's form (the panel's select, or ?run= for OBS; ?attack=1 as before): practice and qualifying, 90 s from each pilot's
// holeshot, a dashed line where it ends; the fastest attack, one timed lap (HS and L1 only); a race, a mass start over 4 laps
// (HS and L1-L4, the 4th marked FIN, each row's place - laps done, then who passed first - before its name)
const RUN_LAPS: Record<string, number> = { qual: 0, attack: 1, race: 4 }
const runSel = $<HTMLSelectElement>('run')
runSel.value = QS.get('run') ?? (QS.has('attack') && QS.get('attack') !== '0' ? 'attack' : stored.run ?? 'qual'); if (!(runSel.value in RUN_LAPS)) runSel.value = 'qual'
runSel.onchange = () => { store({ run: runSel.value }); runSel.blur() }
const showLaps = () => { tlBox.style.display = lapsIn.checked ? '' : 'none' }; showLaps()   // turned off: its room goes too (an empty one is kept otherwise)
lapsIn.onchange = () => { store({ laps: lapsIn.checked }); showLaps() }
function drawTimeline(tc: number) {
  const now = [...pilots.values()].some(p => p.seen.length && p.seen[p.seen.length - 1][1] === tc)   // someone on screen this frame
  // a new heat: someone shows up after QUIET s with no one - or, since goggles left on keep a cell's picture between heats
  // (JDL R7: never 20 s of quiet), a pilot lifting off the pads once the heat manager has moved on, or with no heat manager
  // once the last run is over (90 + 20 s after its first holeshot)
  const key = `${names.heat ?? ''}|${names.cls ?? ''}`; if (key !== heat.nkey) { heat.nkey = key; heat.nchanged = tc }
  const left = Math.max(...[...pilots.values()].map(p => p.leftPad)), named = !!names.heat
  const next = heat.open !== null && left === tc && (named ? heat.nchanged > heat.open && left > heat.nchanged : heat.hs0 !== null && tc - heat.hs0 > RACE + 20)
  if ((now && (heat.open === null || tc - heat.last > QUIET)) || next) {
    heat.open = next ? tc - 1 : tc; heat.hs0 = null
    for (const p of pilots.values()) { p.seen = p.seen.filter(sg => sg[1] >= heat.open!); p.laps = []; p.launch = null; p.hs = null; p.splits = []; p.arc = null; p.lap = 0; p.done = 0; p.lap1 = null }
  }
  if (now) heat.last = tc
  if (heat.open !== null) passes = passes.filter(x => x.tc >= heat.open! - 5)   // a pass a little before the first picture is this heat's
  const ps = inOrder().filter(p => p.seen.length || p.laps.length), tx = matchPasses(ps)
  const rows = ps.map(p => ({ p, laps: tx.has(p) ? lapsOf(passes.filter(x => x.tx === tx.get(p))) : p.laps, dec: tx.has(p) }))
  // a pilot's start: the last moment on the launch area within 20 s before its first pass (a qualifier lifts off and goes
  // straight through the start gate, 3-6 s later at JDL R7, a novice up to 15 s; earlier comings and goings on the pads - carried there, a hop -
  // are left out by looking back from the pass). The heat starts at the first start; each pilot's 90 s at its own first pass (starts are staggered)
  // The holeshot is the first pass with a start before it: a pass with none (the last heat's pilot flying back through the
  // gate on that cell, a drone carried past it) is not this run's, and is left off the row
  for (const r of rows) {
    if (r.p.launch === null) for (const [at] of r.laps) {
      const on = r.p.pad.filter(x => x[1] && x[0] <= at && at - x[0] <= 20); if (on.length) { r.p.launch = on[on.length - 1][0]; r.p.hs = at; break }
    }
    if (r.p.hs !== null) { const k = r.laps.findIndex(l => l[0] >= r.p.hs!); r.laps = r.laps.slice(k).map((l, i) => i ? l : [l[0], NaN]) }
    const lim = RUN_LAPS[runSel.value]; if (lim) r.laps = r.laps.slice(0, lim + 1)   // the holeshot and the run's laps
  }
  const gate1 = rows.flatMap(r => r.laps.length ? [r.laps[0][0]] : []), starts = rows.flatMap(r => r.p.launch !== null ? [r.p.launch] : [])   // with no start seen: the first passes
  const t0 = starts.length ? Math.min(...starts) : gate1.length ? Math.min(...gate1) : null
  heat.hs0 = gate1.length ? Math.min(...gate1) : null
  if (runSel.value === 'race' || runSel.value === 'qual') { drawRace(ps, tc); return }
  tlBox.hidden = !gate || !lapsIn.checked || heat.open === null || !ps.length || t0 === null; if (tlBox.hidden) return
  // drawn at the text size: the whole timeline scales, laid out on a canvas ts times narrower
  const ts = Number(tsIn.value) || 1, W = tlC.clientWidth / ts, ROW = 26, H = 4 * ROW + 6, r = (window.devicePixelRatio || 1) * ts
  if (tlC.width !== Math.round(W * r) || tlC.height !== Math.round(H * r)) { tlC.width = Math.round(W * r); tlC.height = Math.round(H * r); tlC.style.height = H * ts + 'px' }
  const g = tlC.getContext('2d')!; g.setTransform(r, 0, 0, r, 0, 0); g.clearRect(0, 0, W, H)
  const NAME = 170, x0 = NAME, x1 = W - 4, X = (s: number) => x0 + Math.min(1, Math.max(0, (s - t0!) / TL)) * (x1 - x0)
  g.textBaseline = 'middle'
  const race = runSel.value === 'race', place = new Map<Pilot, number>()   // a race's places now: laps done, then the earlier last pass
  if (race) rows.map(r => ({ p: r.p, n: Math.max(0, r.laps.length - 1), last: r.laps.length ? r.laps[r.laps.length - 1][0] : Infinity }))
    .sort((a, b) => b.n - a.n || a.last - b.last).forEach((x, i) => place.set(x.p, i + 1))
  rows.forEach(({ p, laps, dec }, k) => {
    const y = 3 + k * ROW + ROW / 2, who = names.cells && Object.hasOwn(names.cells, p.name) ? String(names.cells[p.name]) : ''
    g.fillStyle = p.color; g.fillRect(0, y - 8, 4, 16)
    g.fillStyle = '#0c0f14'; g.font = '700 13px "Helvetica Neue", "Hiragino Sans", sans-serif'; g.fillText((race && laps.length ? `P${place.get(p)}  ` : '') + (who ? `${p.name}  ${who}` : p.name), 10, y, NAME - 18)
    g.fillStyle = 'rgba(12, 15, 20, .1)'; g.fillRect(x0, y + 3, x1 - x0, 5)
    g.fillStyle = p.color; g.globalAlpha = 0.6
    for (const [a, b] of p.seen) { if (b < t0!) continue; const xa = X(a); g.fillRect(xa, y + 3, X(b) - xa, 5) }
    // each lap its own block, strong and light by turns, so a lap's length reads at a glance; the one under way runs to now
    const ts = laps.map(l => l[0]).filter(t => t >= t0!), seenTo = p.seen.length ? Math.min(tc, p.seen[p.seen.length - 1][1]) : -Infinity
    ts.forEach((a, i) => { const b = i + 1 < ts.length ? ts[i + 1] : seenTo; if (b <= a) return; g.globalAlpha = i % 2 ? 0.4 : 1; g.fillRect(X(a), y + 1, X(b) - X(a), 9) })
    g.globalAlpha = 1; g.font = '400 12px ui-monospace, "SF Mono", Menlo, monospace'
    g.fillStyle = 'rgba(12, 15, 20, .3)'; for (const [at] of p.splits) if (at >= t0!) g.fillRect(X(at) - 0.5, y + 1, 1, 9)   // the other gates: a faint tick across the lap's block (the laps' own lines stay strong)
    if (p.launch !== null) { const x = X(p.launch); g.fillStyle = '#6b7280'; g.fillRect(x - 1, y - 10, 2, 20); g.fillText('GO', x + 4, y - 4) }   // its start
    if (runSel.value === 'qual' && laps.length && laps[0][0] >= t0!) { const x = X(laps[0][0] + RACE); g.fillStyle = 'rgba(12, 15, 20, .55)'; for (let yy = y - 11; yy < y + 11; yy += 5) g.fillRect(x - 0.5, yy, 1, 3) }   // its 90 s from its holeshot, dashed
    let n = 0
    for (const [at, lap] of laps) {
      if (isFinite(lap)) n++; else n = 0                  // the first pass (or the first after a long gap) is the holeshot: a thin line
      if (at < t0! || at > t0! + TL) continue
      const x = X(at); g.fillStyle = dec ? '#a3161a' : '#c2410c'; g.fillRect(x - 1, y - 10, n ? 2 : 1, 20)   // the decoder's red; the page's own orange
      {                                                     // right of the line, or left of it at the right edge; HS: the holeshot, where its 90 s start
        const s = n ? `${race && n === RUN_LAPS.race ? 'FIN' : 'L' + n} ${lap.toFixed(dec ? 3 : 2)}` : 'HS', tw = g.measureText(s).width; g.fillStyle = '#0c0f14'; g.fillText(s, x + 4 + tw > x1 ? x - 4 - tw : x + 4, y - 4)
      }
    }
  })
  if (tc <= t0! + TL) { g.fillStyle = '#a3161a'; g.fillRect(X(tc) - 1, 0, 2, H) }   // the playhead
}

// a race over 4 laps as a row per pilot along the distance, not the time: the bar runs from the start to the finish, a line
// at each lap, the drone a dot at the laps it has run, its place before its name. Finished, it stays at the end, FIN
// a race over 4 laps, or a practice / qualifying run of 90 s over the class's laps (staggered starts: no places, each
// row's seconds left from its holeshot instead). Tiny ticks: the course's other gates on every lap
function drawRace(ps: Pilot[], tc: number) {
  const race = runSel.value === 'race', ql = Object.values(qualLaps)
  const N = race ? RUN_LAPS.race : qualLaps[names.cls ?? ''] ?? (ql.length ? Math.max(...ql) : 4)
  tlBox.hidden = !lapsIn.checked || heat.open === null || !ps.length || line.length < 2; if (tlBox.hidden) return
  const ts = Number(tsIn.value) || 1, W = tlC.clientWidth / ts, ROW = 26, H = 4 * ROW + 6, r = (window.devicePixelRatio || 1) * ts
  if (tlC.width !== Math.round(W * r) || tlC.height !== Math.round(H * r)) { tlC.width = Math.round(W * r); tlC.height = Math.round(H * r); tlC.style.height = H * ts + 'px' }
  const g = tlC.getContext('2d')!; g.setTransform(r, 0, 0, r, 0, 0); g.clearRect(0, 0, W, H)
  const prog = new Map(ps.map(p => { const v = progressOf(p); if (race && v >= N && !p.done) p.done = performance.now(); return [p, p.done ? N : v] as [Pilot, number] }))
  const order = [...ps].sort((a, b) => (a.done && b.done ? a.done - b.done : 0) || prog.get(b)! - prog.get(a)!), place = new Map(order.map((p, i) => [p, i + 1]))
  const label = (p: Pilot) => { const who = names.cells && Object.hasOwn(names.cells, p.name) ? String(names.cells[p.name]) : ''; return (race ? `P${place.get(p)}  ` : '') + (who ? `${p.name}  ${who}` : p.name) }
  g.font = '700 13px "Helvetica Neue", "Hiragino Sans", sans-serif'   // the name column: as wide as this heat's longest name
  const NAME = Math.max(60, ...ps.map(p => g.measureText(label(p)).width + 22)), x0 = NAME, x1 = W - (race ? 40 : 72), X = (v: number) => x0 + Math.min(1, Math.max(0, v / N)) * (x1 - x0)
  const n = line.length, L = lineS[n - 1] + line[n - 1].distance(line[0])
  const gf = splitGates.map(sg => { let k = 0; line.forEach((q, i) => { if (q.distance(sg.c) < line[k].distance(sg.c)) k = i }); return lineS[k] / L })   // where on a lap each gate is
  g.textBaseline = 'middle'
  ps.forEach((p, k) => {
    const y = 3 + k * ROW + ROW / 2, v = prog.get(p)!
    g.fillStyle = p.color; g.fillRect(0, y - 8, 4, 16)
    g.fillStyle = '#0c0f14'; g.font = '700 13px "Helvetica Neue", "Hiragino Sans", sans-serif'; g.fillText(label(p), 10, y)
    g.fillStyle = 'rgba(12, 15, 20, .1)'; g.fillRect(x0, y - 2, x1 - x0, 5)
    g.fillStyle = 'rgba(12, 15, 20, .35)'; for (let i = 1; i <= N; i++) g.fillRect(X(i) - 0.5, y - 9, 1, 18)   // the laps, and the end
    g.fillStyle = p.color; g.fillRect(x0, y - 2, X(v) - x0, 5)
    g.fillStyle = 'rgba(12, 15, 20, .3)'; for (let i = 0; i < N; i++) for (const f of gf) g.fillRect(X(i + f) - 0.5, y - 5, 1, 3)   // the gates, over the bar
    g.beginPath(); g.arc(X(v), y + 0.5, 6, 0, Math.PI * 2); g.fill(); g.strokeStyle = '#fff'; g.lineWidth = 1.5; g.stroke()
    g.fillStyle = '#0c0f14'; g.font = '700 12px ui-monospace, "SF Mono", Menlo, monospace'
    const hs = p.hs ?? p.lap1, left = !race && hs !== null ? Math.ceil(RACE - (tc - hs)) : null   // a run's seconds left: from its holeshot, or its first time past the start
    g.fillText(p.done ? 'FIN' : v > 0 ? `L${Math.min(N, Math.floor(v) + 1)}` + (left !== null && left > 0 ? ` ${left}s` : '') : '', x1 + 8, y)
  })
}

const tmpS = new pc.Vec3(), tmpD = new pc.Vec3()
app.on('update', (dt: number) => {
  const t = nowS()
  cameraAt(t, dt); drawCamSeg()
  if (blendT < BLEND) {                                   // a camera switch glides from where the camera was instead of cutting
    blendT = Math.min(BLEND, blendT + dt); const u = blendT / BLEND, k = u * u * (3 - 2 * u)
    cam.setPosition(new pc.Vec3().lerp(blendFrom.pos, cam.getPosition(), k)); cam.setRotation(new pc.Quat().slerp(blendFrom.rot, cam.getRotation(), k))
  }
  occ?.update(cam.getPosition())
  const eye = cam.getPosition(), pxWorld = 2 * Math.tan(halfFov()) / canvas.clientHeight   // world size of a pixel at unit distance
  let replayRate = 0
  if (REPLAY && rp?.live) {
    const live = rp.live
    if (hasVideo) rt = video.currentTime; else if (playing) rt += dt
    if (rt >= rTo || (hasVideo && video.ended)) jump(rFrom)   // the played part loops
    if (rt < lastRt) { rp.toRaw(); evK = 0; rp.laps.length = 0; rp.seen.length = 0; heat.open = null }   // went back: the revisions are applied again from the start, the laps counted again
    while (evK < events.length && events[evK].due <= rt) rp.revise(events[evK++].win)
    lastRt = rt
    if (document.activeElement !== seek) seek.value = String(rt)
    $('clock').textContent = `${rt.toFixed(1).padStart(6)} / ${rTo.toFixed(0)} s`
    const n = upTo(rt), last = n ? (live.byDone[n - 1] as any) : null
    replayRate = (n - upTo(rt - 2)) / 2; rp.lastLat = last?.lat ?? 0; rp.lastInl = last?.inl ?? 0; rp.lastMsg = t
  }
  drawLine(eye)
  const tc = REPLAY ? rt : t                               // the timeline's clock: the recording's, or this page's
  const tagsAt: { p: Pilot; x: number; y: number }[] = []
  const rows: Node[] = []; let best = 0                  // 1 lost, 2 hold, 3 tracking. No innerHTML: names (from the feed and the heat manager) go in as textContent only
  for (const p of inOrder()) {
    while (p.got.length && p.got[0] < t - 2) p.got.shift()
    const live = p.live
    if (!live || (!REPLAY && !isFinite(p.offset))) { p.hide(); continue }
    const rate = REPLAY ? replayRate : p.got.length / 2, sig = Number(wsIn.value) / 1000; live.sigma = sig
    // live, "now" never steps back: when the fastest recent answer leaves the window the offset grows, and a clock that
    // went back made the trail refit itself from frame 0
    if (!REPLAY) p.tsLive = Math.max(p.tsLive, t - p.offset)
    const ts = REPLAY ? rt : p.tsLive, d = live.dot(ts, 'delay', Number(delayIn.value) / 1000)
    if (!d) { p.hide(); continue }
    const stale = t - p.lastMsg > 1, state = stale || d.state === 'lost' ? 1 : d.state === 'hold' ? 2 : 3; best = Math.max(best, state)
    p.state = state
    { const now = new pc.Vec3(d.pos[0], 0, d.pos[2]); if (p.prev && dt > 0) { const v = now.clone().sub(p.prev).mulScalar(1 / dt); if (v.length() > 1) p.fwd.lerp(p.fwd, v.normalize(), 1 - Math.exp(-dt / 0.3)).normalize() } p.prev = now }
    const col = state === 1 ? cLost : state === 2 ? cHold : p.col
    p.dot.enabled = true; p.dot.setPosition(d.pos[0], d.pos[1], d.pos[2]); p.mat.emissive = col; p.mat.update()
    const sg = p.seen[p.seen.length - 1]; if (sg && tc - sg[1] < 1) sg[1] = tc; else p.seen.push([tc, tc])
    crossing(p, d.pos, state, tc); advance(p, d.pos, state, tc)
    splitGates.forEach((sg, i) => {                         // the other gates, as the start gate's own crossing
      if (state < 2) { p.gss[i] = null; return }
      const q = tmpG.set(d.pos[0], d.pos[1], d.pos[2]).sub(sg.c), sd = q.dot(sg.n), was = p.gss[i] ?? null; p.gss[i] = sd
      if (was === null || Math.sign(sd) === Math.sign(was) || Math.sign(sd - was) !== sg.dir || Math.abs(q.dot(sg.w)) > sg.half || q.y > 5) return
      const last = p.splits.filter(x => x[1] === i).pop(); if (!last || tc - last[0] > 3) p.splits.push([tc, i])
    })
    if (launchArea) {
      const on = Math.hypot(d.pos[0] - launchArea.x, d.pos[2] - launchArea.z) < launchArea.r && d.pos[1] < launchArea.y + 3 ? 1 : 0
      if (!on && p.pad.length && p.pad[p.pad.length - 1][1]) p.leftPad = tc   // just left the pads
      p.pad.push([tc, on]); while (p.pad[0][0] < tc - 30) p.pad.shift()
    }
    if (p.base > p.trailFrom) p.trailFrom = p.base
    let from = Math.max(p.trailFrom, d.frame - Math.round(Number(dur.value) * p.fps))
    if (stale) {                                          // no answers for a second: the trail drains from its old end, as mgp.ts' after a crash, then the pilot goes
      from += Math.round((t - p.lastMsg - 1) * p.fps)
      if (from >= d.frame) { p.hide(); continue }
    }
    if (p.resmooth) { live.trail = new Array(Math.max(0, from)).fill(null); live.trailT = -1; p.resmooth = false }   // only what is drawn is fitted again
    const tr = live.trailAt(ts, d.frame, Math.max(0.5, 3 * sig))   // a point settles once the answers after it are all in (the mean reaches 2.5σ)
    drawTrail(p.trail, tr.slice(from), state === 1 ? cLost : p.col, eye, pxWorld)
    // the name tag, as mgp.ts': over the sphere on screen, hidden when the pilot is behind the camera
    tmpD.set(d.pos[0], d.pos[1], d.pos[2]); const s = cam.camera!.worldToScreen(tmpD, tmpS), behind = cam.forward.dot(tmpD.sub(eye)) < 0
    p.tag.style.display = behind ? 'none' : ''; p.tag.style.left = s.x + 'px'; if (!behind) tagsAt.push({ p, x: s.x, y: s.y }); const b = document.createElement('b'); b.style.background = state === 1 ? '#8c9199' : p.color; b.textContent = p.name   // as mgp.ts: a dark tag, the pilot's colour at its left
    const who = names.cells && Object.hasOwn(names.cells, p.name) ? String(names.cells[p.name]) : '', sm = document.createElement('small'); sm.textContent = stale ? 'NO ANSWERS' : state === 1 ? 'LOST' : ''
    p.tag.replaceChildren(b, ...(who ? [Object.assign(document.createElement('span'), { className: 'who', textContent: who })] : []), ...(sm.textContent ? [sm] : []))
    const nm = names.cells && Object.hasOwn(names.cells, p.name) ? String(names.cells[p.name]) : '', dotS = Object.assign(document.createElement('span'), { textContent: '●' }); dotS.style.color = p.color
    rows.push(document.createTextNode('\n'), dotS, document.createTextNode(` ${p.name.padEnd(4)} ${nm ? nm + '  ' : ''}${rate.toFixed(0).padStart(2)} Hz  ${p.lastLat.toFixed(0).padStart(3)} ms  ${String(p.lastInl).padStart(4)} inl  h ${d.pos[1].toFixed(2)} m`))
  }
  // name tags that would overlap are stacked upwards: the lowest on screen stays over its sphere, each other one goes up
  // past the tags it would cover, and glides there (a tag's box: its sphere's x ± half its width, one tag height above y)
  const boxes: number[][] = []
  // the order sticks: two tags within 30 px of each other keep last frame's order, so pilots at about the same height do
  // not trade places every few frames
  tagsAt.sort((a, b) => Math.abs(a.y - b.y) < 30 ? a.p.order - b.p.order : b.y - a.y); tagsAt.forEach((q, i) => { q.p.order = i })
  for (const q of tagsAt) {
    const w = q.p.tag.offsetWidth, h = q.p.tag.offsetHeight + 2, x0 = q.x - w / 2, x1 = q.x + w / 2
    let y = q.y
    for (let moved = true; moved;) { moved = false; for (const r of boxes) if (x0 < r[1] && x1 > r[0] && y > r[2] - h && y < r[2] + h) { y = r[2] - h; moved = true } }
    boxes.push([x0, x1, y]); q.p.lift += (q.y - y - q.p.lift) * (1 - Math.exp(-dt / 0.15))
    q.p.tag.style.top = q.y - q.p.lift + 'px'
  }
  drawTimeline(tc)
  if (best) { stateEl.textContent = ['', 'LOST', 'HOLD', 'TRACKING'][best]; stateEl.className = ['', '', 'hold', 'ok'][best] }
  else if (REPLAY && rp?.live) { stateEl.textContent = 'NO ANSWER YET'; stateEl.className = '' }
  info.replaceChildren(document.createTextNode(names.heat ? `Heat ${names.heat} ${names.cls ?? ''}${names.stale ? '  (names stale)' : ''}` : ''), ...(names.heat ? rows : rows.slice(1)))
})
app.start()

Object.assign(window, { ghostline: { pilots, app, pc } })   // for checking the page from outside
