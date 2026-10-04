// The live page: the tracker's answers as they are solved (rt_track.py PUSH, server-sent events), drawn on the scan.
//   ?data=<flight folder>   its index.json gives the scene and where the camera starts ("scene", "orbit")
//   ?feed=http://host:port  the tracker's PUSH port
//   ?cam=<regex>            the capture device shown as the live image (its label; default the Guermok USB capture)
//   ?replay=<name>          a recorded flight instead of the feed: <data>/rec/<name>.json (the feed's rows, as an array)
//                           and <name>.mp4 if it is there (what the sender sent). Each answer shows up when it did live
//                           (its frame's time plus the tracker's latency), so the sliders can be tried on the same flight.
//   ?from=30&to=175         the part of the recording that is played (seconds); it loops
// The camera view and the sliders are remembered in this browser (localStorage), so the view left is the view found.
// The dot and the trail are live.ts': the dot 100 ms late, fitted between the answers around it; the trail refitted as
// answers arrive (the dot's delay is the slider's, 100 ms to start with). The clock is the tracker's (when each frame reached it), and "now" is the newest time on it that an
// answer could have arrived for, taken from the fastest recent answer (so a slow answer shows as lag, not as time).
import * as pc from 'playcanvas'
import { LiveDraw, type Answer } from './live'

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const QS = new URLSearchParams(location.search)
const DATA = import.meta.env.BASE_URL + (QS.get('data') ?? 'data') + '/'
const FEED = QS.get('feed') ?? 'http://100.68.63.104:8765'
const REPLAY = QS.get('replay')
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

// --- orbit (drag: orbit, shift or right drag: pan, wheel: dolly), as the viewer's
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
  if (drag.btn === 0 && !e.shiftKey) { orbit.yaw -= dx * 0.3; orbit.pitch = Math.max(-89, Math.min(89, orbit.pitch - dy * 0.3)) }
  else {
    const r = new pc.Quat().setFromEulerAngles(orbit.pitch, orbit.yaw, 0), k = orbit.dist * 0.0015
    orbit.target.sub(r.transformVector(new pc.Vec3(dx * k, 0, 0), tmpV)).add(r.transformVector(new pc.Vec3(0, dy * k, 0), tmpV))
  }
})
canvas.addEventListener('wheel', e => { orbit.dist = Math.max(0.5, orbit.dist * Math.exp(e.deltaY * 0.001)); e.preventDefault(); keepView() }, { passive: false })

// --- scene
fetch(DATA + 'index.json').then(r => r.json()).then(ix => {
  setView(stored.orbit ?? ix.orbit ?? {})               // the view this browser left, else the flight's own start
  $('resetview').onclick = () => { setView(ix.orbit ?? {}); keepView() }
  const a = new pc.Asset(ix.scene, 'gsplat', { url: `${DATA}scene/${ix.scene}.sog` })
  app.assets.add(a); a.on('load', () => { const e = new pc.Entity('scene'); e.addComponent('gsplat', { asset: a }); app.root.addChild(e) }); app.assets.load(a)
})

// --- the drone: a dot and the camera's frustum
const mat = new pc.StandardMaterial(); mat.useLighting = false; mat.emissive = new pc.Color(1, 0.25, 0.1); mat.update()
const dotE = new pc.Entity('dot'); dotE.addComponent('render', { type: 'sphere', material: mat }); dotE.setLocalScale(0.12, 0.12, 0.12); dotE.enabled = false; app.root.addChild(dotE)
const cLive = new pc.Color(1, 0.25, 0.1), cHold = new pc.Color(0.85, 0.55, 0.05), cLost = new pc.Color(0.5, 0.5, 0.5), cTrail = new pc.Color(0.1, 0.45, 0.95)
const X180 = new pc.Quat(1, 0, 0, 0), q = new pc.Quat()
function frustum(pos: number[], quat: number[], col: pc.Color, lines: pc.Vec3[], cols: pc.Color[]) {
  // COLMAP camera axes (x right, y down, z forward) -> PlayCanvas (half turn about x); a 4:3, 100 degree view, 0.4 m deep
  q.set(quat[0], quat[1], quat[2], quat[3]).mul(X180)
  const o = new pc.Vec3(pos[0], pos[1], pos[2]), d = 0.4, hw = d * Math.tan(50 * Math.PI / 180), hh = hw * 3 / 4
  const c = [[-hw, hh], [hw, hh], [hw, -hh], [-hw, -hh]].map(([x, y]) => q.transformVector(new pc.Vec3(x, y, -d), new pc.Vec3()).add(o))
  for (let k = 0; k < 4; k++) { lines.push(o, c[k], c[k], c[(k + 1) % 4]); cols.push(col, col, col, col) }
}

// --- the trail: a band a few pixels wide that faces the camera and thins out towards its old end (WebGL lines are 1 px).
// As race.ts' ribbon, but broken wherever a frame has no position, and drawn with depth: it is opaque, goes into the
// World layer's opaque pass (before the splats) and writes depth, so splats in front of it blend over it and the ones
// behind it are rejected - the trail passes behind desks and shelves instead of lying on top of the picture.
const TRAIL_PX = 5
const trailMesh = new pc.Mesh(app.graphicsDevice)
function emptyTrail() { trailMesh.setPositions([0, 0, 0, 0, 0, 0, 0, 0, 0]); trailMesh.setNormals([0, 1, 0, 0, 1, 0, 0, 1, 0]); trailMesh.setColors(new Array(12).fill(0)); trailMesh.setIndices([0, 1, 2]); trailMesh.update(pc.PRIMITIVE_TRIANGLES) }
trailMesh.clear(true, false); emptyTrail()               // every stream from the start: the shader is built from the first mesh
const trailMat = new pc.StandardMaterial()
{
  const m = trailMat; m.useLighting = false; m.diffuse = new pc.Color(0, 0, 0); m.cull = pc.CULLFACE_NONE
  m.emissiveVertexColor = true; m.emissive = new pc.Color(1, 1, 1); m.blendType = pc.BLEND_NONE; m.depthWrite = true; m.depthTest = true
  // the fade towards the old end is dithered, not blended: a blended band drawn before the splats would mix with the
  // empty background and still hide the splats behind it
  m.opacityVertexColor = true; m.opacityVertexColorChannel = 'a'; m.opacityDither = pc.DITHER_BLUENOISE; m.update()
  const e = new pc.Entity('trail'); e.addComponent('render', { meshInstances: [new pc.MeshInstance(trailMesh, m)] }); app.root.addChild(e)
}
function drawTrail(pts: (number[] | null)[], c: pc.Color, eye: pc.Vec3, pxWorld: number) {
  const pos: number[] = [], col: number[] = [], nrm: number[] = [], idx: number[] = [], n = pts.length
  const tan = new pc.Vec3(), view = new pc.Vec3(), side = new pc.Vec3(), a = new pc.Vec3(), b = new pc.Vec3(), q = new pc.Vec3()
  let run = 0                                         // vertex pairs in the current unbroken run
  for (let k = 0; k < n; k++) {
    const p = pts[k]; if (!p) { run = 0; continue }
    const pa = pts[k - 1] ?? p, pb = pts[k + 1] ?? p
    q.set(p[0], p[1], p[2]); a.set(pa[0], pa[1], pa[2]); b.set(pb[0], pb[1], pb[2])
    tan.sub2(a, b); if (tan.lengthSq() < 1e-12) tan.set(1, 0, 0); tan.normalize(); view.sub2(eye, q)
    const al = k / Math.max(1, n - 1)                // 0 at the old end, 1 at the drone
    const w = TRAIL_PX / 2 * pxWorld * view.length(); side.cross(tan, view.normalize()); if (side.lengthSq() < 1e-12) side.set(0, 1, 0); side.normalize().mulScalar(w)
    pos.push(q.x + side.x, q.y + side.y, q.z + side.z, q.x - side.x, q.y - side.y, q.z - side.z); col.push(c.r, c.g, c.b, al, c.r, c.g, c.b, al); nrm.push(0, 1, 0, 0, 1, 0)
    if (run) { const i = pos.length / 3 - 2; idx.push(i - 2, i - 1, i, i - 1, i + 1, i) }
    run++
  }
  if (idx.length < 3) { emptyTrail(); return }
  trailMesh.setPositions(pos); trailMesh.setNormals(nrm); trailMesh.setColors(col); trailMesh.setIndices(idx); trailMesh.update(pc.PRIMITIVE_TRIANGLES)
}
let resmooth = true                                   // fit the visible trail again from the answers
const dur = $<HTMLInputElement>('dur'); if (stored.trail) dur.value = String(stored.trail)
const showDur = () => { $('durv').textContent = Number(dur.value).toFixed(1).padStart(4) + ' s' }; showDur()
dur.oninput = () => { showDur(); store({ trail: Number(dur.value) }); resmooth = true }   // a longer trail reaches back past what was kept
// smoothing: the seconds either side that each trail point (and the dot) is fitted over, weights tapered to the edge
const wsIn = $<HTMLInputElement>('ws'); if (stored.ws) wsIn.value = String(stored.ws)
const showWs = () => { $('wsv').textContent = Number(wsIn.value).toFixed(2) + ' s' }; showWs()
wsIn.oninput = () => { showWs(); store({ ws: Number(wsIn.value) }); resmooth = true }
// delay: how far behind real time the dot and the trail's head are drawn (live.ts 'delay'). The fit at the head only has
// the answers that have arrived: with a delay shorter than the smoothing window it leans on the past side and the head
// wobbles before it settles; a longer delay steadies it and the dot lags by that much.
const delayIn = $<HTMLInputElement>('delay'); if (stored.delay !== undefined) delayIn.value = String(stored.delay)
const showDelay = () => { $('delayv').textContent = delayIn.value.padStart(3) + ' ms' }; showDelay()
delayIn.oninput = () => { showDelay(); store({ delay: Number(delayIn.value) }) }

// --- the live image: the capture device itself, read by the browser (it is on this machine; the tracker gets the same
// frames over SRT). Raw and undelayed, so it runs ahead of the dot by the tracker's latency plus the dot's delay.
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
if (!REPLAY) startVideo()

// --- the camera's frustum, drawn as the trail is: bands (app.drawLines comes after the splats and cannot be hidden by
// them), opaque, in the pass before the splats, writing depth
const FRUSTUM_PX = 2
const frMesh = new pc.Mesh(app.graphicsDevice), frMat = new pc.StandardMaterial()
function emptyFr() { frMesh.setPositions([0, 0, 0, 0, 0, 0, 0, 0, 0]); frMesh.setNormals([0, 1, 0, 0, 1, 0, 0, 1, 0]); frMesh.setIndices([0, 1, 2]); frMesh.update(pc.PRIMITIVE_TRIANGLES) }
frMesh.clear(true, false); emptyFr()
frMat.useLighting = false; frMat.diffuse = new pc.Color(0, 0, 0); frMat.cull = pc.CULLFACE_NONE; frMat.depthWrite = true; frMat.depthTest = true; frMat.update()
{ const e = new pc.Entity('frustum'); e.addComponent('render', { meshInstances: [new pc.MeshInstance(frMesh, frMat)] }); app.root.addChild(e) }
function drawSegments(pts: pc.Vec3[], col: pc.Color, eye: pc.Vec3, pxWorld: number) {   // pts: pairs of segment ends
  const pos: number[] = [], nrm: number[] = [], idx: number[] = [], dir = new pc.Vec3(), view = new pc.Vec3(), side = new pc.Vec3()
  for (let k = 0; k + 1 < pts.length; k += 2) {
    dir.sub2(pts[k + 1], pts[k]); if (dir.lengthSq() < 1e-12) continue
    for (const q of [pts[k], pts[k + 1]]) {
      view.sub2(eye, q); const w = FRUSTUM_PX / 2 * pxWorld * view.length()
      side.cross(dir, view); if (side.lengthSq() < 1e-12) side.set(0, 1, 0); side.normalize().mulScalar(w)
      pos.push(q.x + side.x, q.y + side.y, q.z + side.z, q.x - side.x, q.y - side.y, q.z - side.z); nrm.push(0, 1, 0, 0, 1, 0)
    }
    const i = pos.length / 3 - 4; idx.push(i, i + 1, i + 2, i + 1, i + 3, i + 2)
  }
  if (!idx.length) { emptyFr(); return }
  frMesh.setPositions(pos); frMesh.setNormals(nrm); frMesh.setIndices(idx); frMesh.update(pc.PRIMITIVE_TRIANGLES)
  frMat.emissive = col; frMat.update()
}

// --- the tracker's revisions (rt_track.py BA): with every answer it sends the poses of its BA window solved again
// together ("win": [frame, x, y, z, qx, qy, qz, qw]). An answer is drawn at the newest revision that has arrived; the raw
// answer is kept so the box can be unticked to compare.
type Stored = Answer & { raw: { pos: number[]; quat: number[] }; src: number }
const byFrame = new Map<number, Stored>()             // the tracker's frame number -> the answer
const baIn = $<HTMLInputElement>('ba'); if (stored.ba === false) baIn.checked = false
const revise = (win: number[][] | undefined) => { if (win && baIn.checked) for (const w of win) { const a = byFrame.get(w[0]); if (a) { a.pos = w.slice(1, 4); a.quat = w.slice(4, 8) } } }
const toRaw = () => { for (const a of byFrame.values()) { a.pos = a.raw.pos; a.quat = a.raw.quat } }
let events: { due: number; win: number[][] }[] = [], evK = 0, lastRt = 0   // replay: the revisions, in the order they came

// --- the feed
let trailFrom = 0
let live: LiveDraw | null = null, offset = Infinity, fps = 60, lastMsg = 0, lastLat = 0, lastInl = 0, base = 0
const got: number[] = []                              // arrival times of solved answers, for the rate
const nowS = () => performance.now() / 1000
let lastTau = 0; const offs: number[][] = []
let tsLive = -Infinity
function reset() { live = null; offset = Infinity; tsLive = -Infinity; got.length = 0; offs.length = 0; base = 0; trailFrom = 0; byFrame.clear() }
baIn.onchange = () => { store({ ba: baIn.checked }); toRaw(); evK = 0; lastRt = 0; resmooth = true }   // live: revisions start again with the next answer
$('clear').onclick = () => { byFrame.clear(); if (live) { base = live.byDone.length ? live.byDone[live.byDone.length - 1].i : 0; live = new LiveDraw([], fps); live.taper = true; resmooth = true } }
function connect() {
  const es = new EventSource(FEED)
  es.onopen = () => { reset(); stateEl.textContent = 'WAITING'; stateEl.className = 'hold' }
  es.onerror = () => { stateEl.textContent = 'NO FEED'; stateEl.className = '' }   // EventSource retries by itself
  es.onmessage = ev => {
    // The stream's clock is the tracker's: an answer's frame arrived there at done - lat. Counting frames instead
    // (i / fps) runs slow against real time - the capture delivers 59.2 fps, not 60 - and the page's "now" then creeps
    // ahead of the newest answer until everything reads as lost. Frames are numbered on that clock (tau * fps).
    const m = JSON.parse(ev.data), t = nowS(); lastMsg = t; fps = m.fps
    const tau = m.done - (m.lat ?? 0) / 1000, fi = Math.round(tau * fps)
    if (tau < lastTau - 1) reset()                    // the tracker started again
    lastTau = tau
    if (!live) { live = new LiveDraw([], fps); live.taper = true; resmooth = true }
    // "now" on the stream's clock: local time minus the smallest (local - tau) of the last few seconds, i.e. the fastest
    // recent answer. A window, not an all-time minimum, so one burst after a stall does not shift the clock for good.
    offs.push([t, t - tau]); while (offs[0][0] < t - 5) offs.shift()
    offset = Math.min(...offs.map(o => o[1]))
    if (m.how === 'none') return
    while (live.byDone.length && live.byDone[0].i < fi - 70 * fps) byFrame.delete((live.byDone.shift() as Stored).src)   // older than any trail the slider allows
    const prev = live.byDone.length ? live.byDone[live.byDone.length - 1].done : -Infinity   // byDone is searched by done: keep it in order when the offset grows
    const a = { i: fi, done: Math.max(t - offset, prev), pos: m.pos, quat: m.quat, inl: m.inl, how: m.how, raw: { pos: m.pos, quat: m.quat }, src: m.i } as Stored
    live.byDone.push(a); byFrame.set(m.i, a); revise(m.win)
    got.push(t); lastLat = m.lat ?? 0; lastInl = m.inl
  }
}
// --- replay: the recording's own clock (the video's when there is one), every answer due at frame time + latency
let rt = 0, rEnd = 0, rFrom = 0, rTo = 0, playing = true, hasVideo = false, due: number[] = []
const video = $<HTMLVideoElement>('video'), seek = $<HTMLInputElement>('seek')
async function loadReplay(name: string) {
  const rows: any[] = await fetch(`${DATA}rec/${name}.json`).then(r => r.json())
  // the capture's real rate (59.2, not the nominal 60): frames counted against the tracker's clock
  const a = rows[0], b = rows[rows.length - 1], tau = (m: any) => m.done - (m.lat ?? 0) / 1000
  fps = (b.i - a.i) / (tau(b) - tau(a)); rEnd = (b.i - a.i) / fps
  // frames from the recording's first row (a recording made with the video starts at the tracker's frame 0 anyway)
  const ans = rows.filter(m => m.how !== 'none').map(m => ({ i: m.i - a.i, done: (m.i - a.i) / fps + (m.lat ?? 0) / 1000, pos: m.pos, quat: m.quat, inl: m.inl, how: m.how, lat: m.lat,
    raw: { pos: m.pos, quat: m.quat }, src: m.i }))
  for (const x of ans) byFrame.set(x.src, x as unknown as Stored)
  events = rows.filter(m => m.win).map(m => ({ due: (m.i - a.i) / fps + (m.lat ?? 0) / 1000, win: m.win as number[][] })).sort((x, y) => x.due - y.due)
  baIn.parentElement!.hidden = !events.length          // a recording made without BA has nothing to switch
  live = new LiveDraw(ans as Answer[], fps); live.taper = true; due = live.byDone.map(x => x.done)
  rFrom = Math.min(rEnd, Number(QS.get('from') ?? Math.max(0, (ans[0]?.i ?? 0) / fps - 1))); rTo = Math.min(rEnd, Number(QS.get('to') ?? rEnd))
  rt = rFrom; seek.min = String(QS.has('from') ? rFrom : 0); seek.max = String(rTo); $('replaybar').hidden = false
  video.src = `${DATA}rec/${name}.mp4`
  video.onloadedmetadata = () => { hasVideo = true; video.currentTime = rt; video.play(); $('dvrinfo').textContent = 'recorded' }
  video.onerror = () => { $('dvrinfo').textContent = 'no video in this recording' }
  jump = (v: number) => { rt = v; if (hasVideo) { video.currentTime = v; if (playing) video.play() } resmooth = true }
  seek.oninput = () => jump(Number(seek.value))
  $('play').onclick = () => { playing = !playing; $('play').textContent = playing ? 'pause' : 'play'; if (hasVideo) { if (playing) video.play(); else video.pause() } }
  window.addEventListener('keydown', e => { if (e.key === ' ') { $('play').click(); e.preventDefault() } if (e.key === 'ArrowLeft') jump(Math.max(Number(seek.min), rt - 5)); if (e.key === 'ArrowRight') jump(Math.min(rTo, rt + 5)) })
}
let jump = (_: number) => {}
const upTo = (v: number) => { let lo = 0, hi = due.length; while (lo < hi) { const m = (lo + hi) >> 1; if (due[m] <= v) lo = m + 1; else hi = m } return lo }   // answers due by v
if (REPLAY) loadReplay(REPLAY).catch(() => { stateEl.textContent = 'NO RECORDING'; stateEl.className = '' }); else connect()

app.on('update', (dt: number) => {
  const r = new pc.Quat().setFromEulerAngles(orbit.pitch, orbit.yaw, 0)
  cam.setPosition(r.transformVector(new pc.Vec3(0, 0, orbit.dist), new pc.Vec3()).add(orbit.target)); cam.setRotation(r)
  const t = nowS(); while (got.length && got[0] < t - 2) got.shift()
  if (!live || (!REPLAY && !isFinite(offset))) return
  let rate = got.length / 2
  if (REPLAY) {
    if (hasVideo) rt = video.currentTime; else if (playing) rt += dt
    if (rt >= rTo || (hasVideo && video.ended)) jump(rFrom)   // the played part loops
    if (rt < lastRt) { toRaw(); evK = 0 }             // went back: the revisions are applied again from the start
    while (evK < events.length && events[evK].due <= rt) revise(events[evK++].win)
    lastRt = rt
    if (document.activeElement !== seek) seek.value = String(rt)
    $('clock').textContent = `${rt.toFixed(1).padStart(6)} / ${rTo.toFixed(0)} s`
    const n = upTo(rt), last = n ? (live.byDone[n - 1] as any) : null
    rate = (n - upTo(rt - 2)) / 2; lastLat = last?.lat ?? 0; lastInl = last?.inl ?? 0; lastMsg = t
  }
  const ws = Number(wsIn.value); live.ws = ws
  // live, "now" never steps back: when the fastest recent answer leaves the window the offset grows, and a clock that
  // went back made the trail refit itself from frame 0
  if (!REPLAY) tsLive = Math.max(tsLive, t - offset)
  const ts = REPLAY ? rt : tsLive, d = live.dot(ts, 'delay', Number(delayIn.value) / 1000)
  const lines: pc.Vec3[] = [], cols: pc.Color[] = []
  if (!d) { dotE.enabled = false; emptyTrail(); emptyFr(); if (REPLAY) { stateEl.textContent = 'NO ANSWER YET'; stateEl.className = ''; info.textContent = '' } }
  if (d) {
    const stale = t - lastMsg > 1, col = stale || d.state === 'lost' ? cLost : d.state === 'hold' ? cHold : cLive
    dotE.enabled = true; dotE.setPosition(d.pos[0], d.pos[1], d.pos[2]); mat.emissive = col; mat.update()
    frustum(d.pos, d.quat, col, lines, cols)
    stateEl.textContent = stale ? 'NO ANSWERS' : d.state === 'lost' ? 'LOST' : d.state === 'hold' ? 'HOLD' : 'TRACKING'
    stateEl.className = stale || d.state === 'lost' ? '' : d.state === 'hold' ? 'hold' : 'ok'
    if (base > trailFrom) trailFrom = base
    const from = Math.max(trailFrom, d.frame - Math.round(Number(dur.value) * fps))
    if (resmooth) { live.trail = new Array(Math.max(0, from)).fill(null); live.trailT = -1; resmooth = false }   // only what is drawn is fitted again
    const tr = live.trailAt(ts, d.frame, Math.max(0.5, 2 * ws) + 0.6)   // a point settles once the answers after it, and their revisions, are all in
    // a new dither pattern every frame, so the thinned-out end shimmers into a fade instead of showing fixed dots (the
    // engine only moves the pattern for a jittered camera, i.e. with TAA)
    trailMat.setParameter('blueNoiseJitter', [Math.random(), Math.random(), Math.random(), Math.random()])
    drawTrail(tr.slice(from), cTrail, cam.getPosition(), 2 * Math.tan(30 * Math.PI / 180) / canvas.clientHeight)   // world size of a pixel at unit distance (fov 60)
    info.textContent = `${rate.toFixed(0).padStart(2)} Hz  ${lastLat.toFixed(0).padStart(3)} ms  ${String(lastInl).padStart(4)} inl  h ${d.pos[1].toFixed(2)} m`
  }
  if (lines.length) drawSegments(lines, cols[0], cam.getPosition(), 2 * Math.tan(30 * Math.PI / 180) / canvas.clientHeight)
})
app.start()
