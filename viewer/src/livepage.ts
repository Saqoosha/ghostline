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
// The sliders and the Free camera's view are remembered in this browser (localStorage); the page always opens on Orbit.
// The dot and the trail are live.ts': the dot 100 ms late, fitted between the answers around it; the trail refitted as
// answers arrive (the dot's delay is the slider's, 100 ms to start with). The clock is the tracker's (when each frame reached it), and "now" is the newest time on it that an
// answer could have arrived for, taken from the fastest recent answer (so a slow answer shows as lag, not as time).
import * as pc from 'playcanvas'
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

const canvas = $<HTMLCanvasElement>('c'), stateEl = $('state'), info = $('info')
const app = new pc.Application(canvas, { mouse: new pc.Mouse(canvas), graphicsDeviceOptions: { antialias: false } })
app.setCanvasFillMode(pc.FILLMODE_FILL_WINDOW); app.setCanvasResolution(pc.RESOLUTION_AUTO)
window.addEventListener('resize', () => app.resizeCanvas())
const cam = new pc.Entity('cam')
cam.addComponent('camera', { clearColor: new pc.Color(0.91, 0.92, 0.94), fov: 60, nearClip: 0.1, farClip: 500 })
app.root.addChild(cam)

// --- Free mode: drag orbits, shift or right drag pans, the wheel dollies (as the viewer's). The other modes: drag turns and tilts, the wheel zooms
const orbit = { target: new pc.Vec3(0, 1, 0), yaw: 0, pitch: -45, dist: 12 }, tmpV = new pc.Vec3()
let drag: { x: number; y: number; btn: number } | null = null
canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, btn: e.button }; canvas.setPointerCapture(e.pointerId) })
const keepView = () => store({ orbit: { target: [orbit.target.x, orbit.target.y, orbit.target.z], dist: orbit.dist, yaw: orbit.yaw, pitch: orbit.pitch } })
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
  else view.zoom = Math.max(0.4, Math.min(3, view.zoom * Math.exp(e.deltaY * 0.001)))
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
  camMode = v; drawCamSeg()
}
const camSeg = $('camseg'); let segKey = ''
function drawCamSeg() {                                    // Orbit / Top / Free, then a follow button per pilot heard
  const ps = [...pilots.values()], key = camMode + '|' + ps.map(p => p.name + p.color).join(',')   // a colour can change once names arrive
  if (key === segKey) return; segKey = key
  const btn = (v: string, label: string, color?: string) => { const b = document.createElement('button'); b.textContent = label; b.className = v === camMode ? 'on' : ''
    if (color) b.style.borderLeft = `6px solid ${color}`; b.onclick = () => { setMode(v === camMode && v[0] === 'p' ? 'orbit' : v); b.blur() }; return b }
  camSeg.replaceChildren(btn('orbit', 'Orbit'), btn('top', 'Top'), btn('free', 'Free'), ...ps.map(p => btn('p' + p.name, p.name, p.color)))
}
window.addEventListener('keydown', e => {
  if ((e.target as HTMLElement).tagName === 'INPUT') return
  const k = Number(e.key); if (!(k >= 1)) return
  const ps = [...pilots.values()], v = k <= 3 ? ['orbit', 'top', 'free'][k - 1] : ps[k - 4] ? 'p' + ps[k - 4].name : ''
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
  const tanV = Math.tan(30 * Math.PI / 180) * 0.85, tanH = tanV * canvas.clientWidth / Math.max(1, canvas.clientHeight)
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
  }).catch(e => console.error('course line', e))     // the page still works: no line, and the orbit turns round the pilots
  $('resetview').onclick = () => { setView(ix.orbit ?? {}); keepView(); Object.assign(view, { yawOffset: 30, topYaw: 0, pitch: PITCH0, zoom: 1 }); orb.init = false; setMode('orbit') }
  const a = new pc.Asset(ix.scene, 'gsplat', { url: `${DATA}scene/${ix.scene}.sog` })
  app.assets.add(a); a.on('load', () => {
    const e = new pc.Entity('scene'); e.addComponent('gsplat', { asset: a }); app.root.addChild(e)
    if (ix.room) {
      const box = $<HTMLInputElement>('seein'); box.parentElement!.hidden = false; box.checked = stored.seein !== false
      occ = new RoomOcclusion(e.gsplat!, ix.room); occ.enabled = box.checked
      box.onchange = () => { occ!.enabled = box.checked; store({ seein: box.checked }) }
    }
  }); a.on('error', fail('scene')); app.assets.load(a)
}).catch(fail('index.json'))
let occ: RoomOcclusion | null = null                   // the scene entity sits at the origin: the camera's position is in its frame


// --- the pilots: one per cell of the feed (rt_track.py's "stream"), drawn as the MGP page draws them (mgp.ts): a small sphere,
// a trail in the pilot's colour that thins and fades to its old end, and a name tag. Each keeps its own answers and its own clock.
const PALETTE = ['#ff3b30', '#34c759', '#0a84ff', '#ff9f0a']   // the FDF semifinal race.json's three, then orange
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
  // the fade towards the old end is dithered, not blended: a blended band drawn before the splats would mix with the
  // empty background and still hide the splats behind it (mgp.ts uses alpha-to-coverage; this page has no MSAA)
  m.opacityVertexColor = true; m.opacityVertexColorChannel = 'a'; m.opacityDither = pc.DITHER_BLUENOISE; m.update()
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
  base = 0; trailFrom = 0; resmooth = true; state = 0; fwd = new pc.Vec3(0, 0, -1); prev: pc.Vec3 | null = null   // state: 1 lost, 2 hold, 3 tracking; fwd: the level heading, for follow
  offs: number[][] = []; got: number[] = []                     // (local, local - tau) of recent answers; arrival times, for the rate
  byFrame = new Map<number, Stored>()                            // the tracker's frame number -> the answer
  mat = new pc.StandardMaterial(); dot = new pc.Entity('dot'); trail = trailMesh(); tag = document.createElement('div')
  // the colour follows the cell's place in the heat manager's list (the config's order), so every viewer sees the same; arrival order without it
  get color() { const k = names.cells ? Object.keys(names.cells).indexOf(this.name) : -1; return PALETTE[(k >= 0 ? k : this.seq) % PALETTE.length] }
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
  hide() { this.state = 0; this.dot.enabled = false; this.tag.style.display = 'none'; emptyTrail(this.trail) }
}
const pilots = new Map<string, Pilot>()
// who flies each cell: rt_control.py's /api/pilots (the venue's heat manager), every 5 s; nothing when it is not there
let names: { cells?: Record<string, string>; heat?: string; cls?: string; stale?: boolean } = {}
let namesErr = ''
const pollNames = () => fetch('/api/pilots').then(r => { if (r.status === 404) { clearInterval(namesTimer); throw new Error('no /api/pilots here') } if (!r.ok) throw new Error(String(r.status)); return r.json() })
  .then(j => { names = j ?? {}; namesErr = '' }).catch(e => { if (String(e) !== namesErr) console.warn('pilot names:', e); namesErr = String(e); names = { ...names, stale: true } })   // keep the last names: colours stay put
const namesTimer = REPLAY ? 0 : setInterval(pollNames, 5000); if (!REPLAY) pollNames()
// a flag beside each name, as mgp.ts': the heat manager gives names only, so the country is guessed from the name - kana or
// kanji: JP; a Korean family name among the romanized words: KR - unless <data>/countries.json ({name: ISO-2}) says
// (not the ones that are also romanized Japanese or English: yu, ko, go, jin, han, jo, an, min, son, ha, na, ji, im)
const KR = new Set('kim lee yi park choi jung jeong kang cho yoon yun jang lim oh seo shin kwon hwang ahn song yoo hong jeon moon yang bae baek heo nam noh kwak sung cha joo woo ryu uhm chae won byun bang yeo pyo myung'.split(' '))
let countries: Record<string, string> = {}
fetch(DATA + 'countries.json').then(r => r.ok ? r.json() : {}).then(j => { countries = j ?? {} }).catch(e => console.warn('countries.json', e))   // optional
const country = (name: string) => (Object.hasOwn(countries, name) ? countries[name] : null) ?? (/[\u3040-\u30ff\u4e00-\u9fff]/.test(name) ? 'JP' : name.toLowerCase().split(/[\s-]+/).some(w => KR.has(w)) ? 'KR' : '')
const flagOf = (cc: string) => /^[A-Z]{2}$/.test(cc) ? String.fromCodePoint(...[...cc].map(c => 0x1f1e6 + c.charCodeAt(0) - 65)) : ''
const pilot = (name: string) => { let p = pilots.get(name); if (!p) { p = new Pilot(name, pilots.size); pilots.set(name, p) } return p }
const resmoothAll = () => { for (const p of pilots.values()) p.resmooth = true }   // fit the visible trails again from the answers

const dur = $<HTMLInputElement>('dur'); if (stored.trail) dur.value = String(stored.trail)
const showDur = () => { $('durv').textContent = Number(dur.value).toFixed(1).padStart(4) + ' s' }; showDur()
dur.oninput = () => { showDur(); store({ trail: Number(dur.value) }); resmoothAll() }   // a longer trail reaches back past what was kept
// smoothing: the seconds either side that each trail point (and the dot) is fitted over, weights tapered to the edge
const wsIn = $<HTMLInputElement>('ws'); if (stored.ws) wsIn.value = String(stored.ws)
const showWs = () => { $('wsv').textContent = Number(wsIn.value).toFixed(2) + ' s' }; showWs()
wsIn.oninput = () => { showWs(); store({ ws: Number(wsIn.value) }); resmoothAll() }
// delay: how far behind real time the dot and the trail's head are drawn (live.ts 'delay'). The fit at the head only has
// the answers that have arrived: with a delay shorter than the smoothing window it leans on the past side and the head
// wobbles before it settles; a longer delay steadies it and the dot lags by that much.
const delayIn = $<HTMLInputElement>('delay'); if (stored.delay !== undefined) delayIn.value = String(stored.delay)
const showDelay = () => { $('delayv').textContent = delayIn.value.padStart(3) + ' ms' }; showDelay()
delayIn.oninput = () => { showDelay(); store({ delay: Number(delayIn.value) }) }

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
    const live = p.live
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

const tmpS = new pc.Vec3(), tmpD = new pc.Vec3()
app.on('update', (dt: number) => {
  const t = nowS()
  cameraAt(t, dt); drawCamSeg()
  if (blendT < BLEND) {                                   // a camera switch glides from where the camera was instead of cutting
    blendT = Math.min(BLEND, blendT + dt); const u = blendT / BLEND, k = u * u * (3 - 2 * u)
    cam.setPosition(new pc.Vec3().lerp(blendFrom.pos, cam.getPosition(), k)); cam.setRotation(new pc.Quat().slerp(blendFrom.rot, cam.getRotation(), k))
  }
  occ?.update(cam.getPosition())
  const eye = cam.getPosition(), pxWorld = 2 * Math.tan(30 * Math.PI / 180) / canvas.clientHeight   // world size of a pixel at unit distance (fov 60)
  let replayRate = 0
  if (REPLAY && rp?.live) {
    const live = rp.live
    if (hasVideo) rt = video.currentTime; else if (playing) rt += dt
    if (rt >= rTo || (hasVideo && video.ended)) jump(rFrom)   // the played part loops
    if (rt < lastRt) { rp.toRaw(); evK = 0 }          // went back: the revisions are applied again from the start
    while (evK < events.length && events[evK].due <= rt) rp.revise(events[evK++].win)
    lastRt = rt
    if (document.activeElement !== seek) seek.value = String(rt)
    $('clock').textContent = `${rt.toFixed(1).padStart(6)} / ${rTo.toFixed(0)} s`
    const n = upTo(rt), last = n ? (live.byDone[n - 1] as any) : null
    replayRate = (n - upTo(rt - 2)) / 2; rp.lastLat = last?.lat ?? 0; rp.lastInl = last?.inl ?? 0; rp.lastMsg = t
  }
  drawLine(eye)
  // a new dither pattern every frame, so the thinned-out end shimmers into a fade instead of showing fixed dots (the
  // engine only moves the pattern for a jittered camera, i.e. with TAA)
  trailMat.setParameter('blueNoiseJitter', [Math.random(), Math.random(), Math.random(), Math.random()])
  const rows: Node[] = []; let best = 0                    // 1 lost, 2 hold, 3 tracking. No innerHTML: names (from the feed and the heat manager) go in as textContent only
  for (const p of pilots.values()) {
    while (p.got.length && p.got[0] < t - 2) p.got.shift()
    const live = p.live
    if (!live || (!REPLAY && !isFinite(p.offset))) { p.hide(); continue }
    const rate = REPLAY ? replayRate : p.got.length / 2, ws = Number(wsIn.value); live.ws = ws
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
    if (p.base > p.trailFrom) p.trailFrom = p.base
    let from = Math.max(p.trailFrom, d.frame - Math.round(Number(dur.value) * p.fps))
    if (stale) {                                          // no answers for a second: the trail drains from its old end, as mgp.ts' after a crash, then the pilot goes
      from += Math.round((t - p.lastMsg - 1) * p.fps)
      if (from >= d.frame) { p.hide(); continue }
    }
    if (p.resmooth) { live.trail = new Array(Math.max(0, from)).fill(null); live.trailT = -1; p.resmooth = false }   // only what is drawn is fitted again
    const tr = live.trailAt(ts, d.frame, Math.max(0.5, 2 * ws))   // a point settles once the answers after it are all in
    drawTrail(p.trail, tr.slice(from), state === 1 ? cLost : p.col, eye, pxWorld)
    // the name tag, as mgp.ts': over the sphere on screen, hidden when the pilot is behind the camera
    tmpD.set(d.pos[0], d.pos[1], d.pos[2]); const s = cam.camera!.worldToScreen(tmpD, tmpS), behind = cam.forward.dot(tmpD.sub(eye)) < 0
    p.tag.style.display = behind ? 'none' : ''; p.tag.style.left = s.x + 'px'; p.tag.style.top = s.y + 'px'; const b = document.createElement('b'); b.style.background = state === 1 ? '#8c9199' : p.color; b.textContent = p.name   // as mgp.ts: a dark tag, the pilot's colour at its left
    const who = names.cells && Object.hasOwn(names.cells, p.name) ? String(names.cells[p.name]) : '', sm = document.createElement('small'); sm.textContent = stale ? 'NO ANSWERS' : state === 1 ? 'LOST' : ''
    p.tag.replaceChildren(b, ...(who ? [Object.assign(document.createElement('span'), { className: 'who', textContent: (flagOf(country(who)) + ' ' + who).trim() })] : []), ...(sm.textContent ? [sm] : []))
    const nm = names.cells && Object.hasOwn(names.cells, p.name) ? String(names.cells[p.name]) : '', dotS = Object.assign(document.createElement('span'), { textContent: '●' }); dotS.style.color = p.color
    rows.push(document.createTextNode('\n'), dotS, document.createTextNode(` ${p.name.padEnd(4)} ${nm ? (flagOf(country(nm)) + ' ' + nm).trim() + '  ' : ''}${rate.toFixed(0).padStart(2)} Hz  ${p.lastLat.toFixed(0).padStart(3)} ms  ${String(p.lastInl).padStart(4)} inl  h ${d.pos[1].toFixed(2)} m`))
  }
  if (best) { stateEl.textContent = ['', 'LOST', 'HOLD', 'TRACKING'][best]; stateEl.className = ['', '', 'hold', 'ok'][best] }
  else if (REPLAY && rp?.live) { stateEl.textContent = 'NO ANSWER YET'; stateEl.className = '' }
  info.replaceChildren(document.createTextNode(names.heat ? `Heat ${names.heat} ${names.cls ?? ''}${names.stale ? '  (names stale)' : ''}` : ''), ...(names.heat ? rows : rows.slice(1)))
})
app.start()

Object.assign(window, { ghostline: { pilots, app, pc } })   // for checking the page from outside
