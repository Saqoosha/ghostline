// 2026 MultiGP Championship: every round's pilots' DVR paths on one 3DGS scan, one round at a time.
// rounds.json in the data folder lists the rounds; each round's folder holds a race.json in the race page's schema
// (race.ts), with optional extras per pilot: country (ISO-2), official_laps (LiveFPV), position (official place).
// The scan and the sky are the data folder's own (scene/<race.scene>.sog, sky.jpg), shared by every round.
import * as pc from 'playcanvas'
import { bakeSky } from './sky'
import { ribbon, ribbonMesh, segment, resortWhenIdle } from './racekit'

type Pilot = { name: string; color: string; video?: string; end: number; crashed: boolean; finish: number | null; lap_ends: number[]; laps: number[]; total: number
  holeshot?: number; holeshot_at?: number; pos: number[][]; country?: string; official_laps?: number[]; position?: number; video_size?: [number, number]; slot?: number }
// a gate: centre at the bottom middle of its opening, width_dir across it, half_width and height of the opening (null for
// a checkpoint that is not a gate: a flag, an invisible marker)
type Gate = { centre: number[]; width_dir?: number[]; normal?: number[]; half_width?: number | null; height?: number | null; start?: boolean; finish?: boolean; source?: string; confidence?: number }
type Race = { title: string; fps: number; start: number; scene: string; sky?: string; laps: number; gate?: Gate; checkpoints?: Gate[]; pilots: Pilot[]; track?: number[][]; track_name?: string; round?: number }
type RoundEntry = { round: number; label?: string; completed?: string; data: string; pilots?: string[]; fastest?: { pilot: string; lap: number } }
type Rounds = { title: string; rounds: RoundEntry[] }

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const QS = new URLSearchParams(location.search)
const DATA_Q = QS.get('data'); const DATA = DATA_Q && /^[a-z0-9_-]+$/i.test(DATA_Q) ? DATA_Q : ''   // a folder name only: no path parts
const BASE = import.meta.env.BASE_URL + (DATA || import.meta.env.VITE_MGP_DATA || 'mgp-dev') + '/'   // VITE_MGP_DATA: the published folder
const TRAIL = 2, TRAIL_PX = 5                                       // seconds of trail behind each drone, its width
const AUDIO_LEAD = 3                                                // audio.m4a starts 3 s before the clip: the countdown (as on the race page)
const FLASH = 2.5                                                   // seconds a finished lap's time stays on the board
const canvas = $<HTMLCanvasElement>('c'), hline = $('hline')
const say = (s: string) => { hline.textContent = s; hline.hidden = !s }   // a line in the round header: loading, a missing scan, an error
const phone = () => innerWidth <= 720, narrow = () => innerWidth < 1000   // phone: standings across the top; narrow: the DVR strip is a row above the bar
const app = new pc.Application(canvas, { mouse: new pc.Mouse(canvas), graphicsDeviceOptions: { antialias: true } })
app.setCanvasFillMode(pc.FILLMODE_NONE); app.setCanvasResolution(pc.RESOLUTION_AUTO)   // the canvas is the window minus the DVR column and the bar (measureFree sizes it)
const cam = new pc.Entity('cam'); cam.addComponent('camera', { clearColor: new pc.Color(0.92, 0.93, 0.95), fov: 50, nearClip: 0.2, farClip: 2000 })
app.root.addChild(cam)

// sky and scan, shared by the rounds (a round naming another scan or sky swaps it: race.sky, the night scan's, else sky.jpg)
// the sky is the scan's surroundings: without the scan it is left out (half of it is the ground under the course)
let sky: pc.Texture | null = null, skyFile = ''
const skies = new Map<string, pc.Texture>()
const showSky = () => { app.scene.skybox = sky && !sceneMissing ? sky : null }
function loadSky(file: string) {
  if (file === skyFile) return
  skyFile = file; sky = skies.get(file) ?? null; showSky()
  if (sky) return
  const img = new Image()
  img.onload = () => { const t = bakeSky(app.graphicsDevice, img); skies.set(file, t); if (skyFile === file) { app.scene.skyboxMip = 0; sky = t; showSky() } }
  img.src = BASE + file
}
let scan: pc.Entity | null = null, sceneName = '', sceneReady = false, sceneMissing = false, sceneFailed = false
const sceneNote = () => sceneFailed ? 'scan failed — reload the page' : sceneMissing ? `no scan of this course yet: flight paths and gates only` : sceneReady ? '' : 'loading scene…'
// the scan's loading overlay: a bar over the canvas driven by the asset's 'progress' (bytes received of the .sog)
const scanLoad = $('scanload'), scanBar = $('scanbar'), scanText = $('scantext')
function showScanLoad(frac: number | null, text = 'Loading scan…') {
  scanLoad.hidden = false; scanText.textContent = text
  scanBar.classList.toggle('busy', frac === null); scanBar.style.width = frac === null ? '' : `${Math.round(frac * 100)}%`
}
// The .sog comes from R2 through a Worker; a download can still stall part way (the clips below were the cause seen).
// A watchdog retries: no progress for STALL_MS (or the asset erroring) drops the asset and loads it again with a
// cache-busting query, up to RETRIES times; a download that reached its end but whose 'load' never comes (the build
// hung) gets the longer BUILD_MS before the same retry. After the last try the overlay and the header say so.
const STALL_MS = 10000, BUILD_MS = 30000, RETRIES = 3
let sceneTry = 0, sceneTimer = 0
// The DVR clips and the sound wait for the scan, and are read whole into blobs instead of streamed by the <video>. They
// share the page's one HTTP/3 connection with everything else, and a <video> that has buffered enough stops reading its
// open bytes=0- request; the unread data then holds the connection's flow-control window and every other request
// stalls behind it: the scan at 13-15 of 23 MB on every load (until the watchdog's retry), and the next round's race.json
// for 12 s on a switch from a day round to a night one (measured 2026-10-20/21). A fetch read to its end leaves no such
// request open; a round switch aborts the reads still running.
let scanSettled = true; const afterScan: (() => void)[] = []
let mediaAbort: AbortController | null = null
function loadMedia(m: HTMLMediaElement, url: string, token: number) {
  const ac = mediaAbort!
  fetch(url, { signal: ac.signal }).then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.blob() })
    .then(b => { if (token === loadToken) m.src = URL.createObjectURL(b) })
    .catch(e => { if (token === loadToken && !ac.signal.aborted) m.dispatchEvent(new Event('error')); if (!ac.signal.aborted) console.warn('media', url, e) })
}
const whenScanSettled = (f: () => void) => { if (scanSettled) f(); else afterScan.push(f) }
const settleScan = () => { scanSettled = true; afterScan.splice(0).forEach(f => f()) }
let scanAsset: pc.Asset | null = null, pendingDrop: (() => void) | null = null   // the shown scan's asset; the loading one's drop
function dropScan() { scan?.destroy(); scan = null; if (scanAsset) { app.assets.remove(scanAsset); scanAsset.unload(); scanAsset = null } }
function loadScene(name: string) {
  if (name === sceneName) return
  pendingDrop?.(); sceneName = name; sceneTry = 0; scanSettled = false; loadSceneTry(name)
}
function loadSceneTry(name: string) {
  sceneReady = false; sceneMissing = false; sceneFailed = false; showSky(); clearTimeout(sceneTimer)
  const retry = sceneTry > 0, label = retry ? `Retrying… (${sceneTry}/${RETRIES})` : 'Loading scan…'
  showScanLoad(null, label)
  const a = new pc.Asset(`${name}#${sceneTry}`, 'gsplat', { url: `${BASE}scene/${name}.sog${retry ? `?r=${sceneTry}` : ''}` }); app.assets.add(a)
  let got = 0, total = 0, building = false, over = false
  const drop = () => { over = true; pendingDrop = null; clearTimeout(sceneTimer); a.off(); app.assets.remove(a); a.unload() }
  pendingDrop = drop
  const fail = (why: string) => {
    if (sceneName !== name || over) return
    console.warn(`scene ${name}: ${why} at ${(got / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB (try ${sceneTry + 1} of ${RETRIES + 1})`); drop()
    if (sceneTry < RETRIES) { sceneTry++; loadSceneTry(name); return }
    sceneFailed = true; dropScan(); showSky(); showScanLoad(null, 'Scan failed — reload the page'); scanBar.classList.remove('busy'); scanBar.style.width = '0'; if (!loadingRound) say(sceneNote())
    settleScan()
  }
  const arm = () => { clearTimeout(sceneTimer); sceneTimer = window.setTimeout(() => fail(building ? 'build hung' : 'download stalled'), building ? BUILD_MS : STALL_MS) }
  arm()
  a.on('progress', (g: number, t: number) => { if (sceneName !== name || over) return
    got = g; total = t; building = t > 0 && g >= t; arm()
    showScanLoad(t > 0 ? g / t : null, building ? (retry ? `Building scan… (retry ${sceneTry}/${RETRIES})` : 'Building scan…') : `${label} ${(g / 1048576).toFixed(0)} / ${(t / 1048576).toFixed(0)} MB`) })
  a.on('load', () => { if (sceneName !== name || over) return
    clearTimeout(sceneTimer); pendingDrop = null; dropScan(); scanAsset = a; const e = new pc.Entity('scan'); e.addComponent('gsplat', { asset: a }); app.root.addChild(e); scan = e; sceneReady = true; topCut = NaN; scanLoad.hidden = true; if (!loadingRound) say(''); settleScan() })
  // a 404 (no scan made yet) is final: the round plays over a ground grid; any other error is retried
  a.on('error', (err: string) => { if (sceneName !== name || over) return
    if (/404|not found/i.test(String(err))) { console.warn('scene', name, err); drop(); dropScan(); sceneMissing = true; showSky(); scanLoad.hidden = true; if (!loadingRound) say(sceneNote()); settleScan(); return }
    fail('error: ' + err) })
  app.assets.load(a)
}

const fetchJson = <T>(url: string): Promise<T> => fetch(url).then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json() })
const rounds: Rounds = await fetchJson<Rounds>(BASE + 'rounds.json').catch((e: Error) => { say(`could not load ${BASE}rounds.json: ${e.message}`); throw e })
if (!rounds.rounds?.length) { say('rounds.json lists no rounds'); throw new Error('no rounds') }
// the menu's sections, in the order they were raced: the qualifiers, then the Mains from E up to A, each Main's races and
// then its final ("<X> Main · Race <n>" / "<X> Main · Final"); prev / next step through the same order
const MAINS = ['E', 'D', 'C', 'B', 'A']
function sectionOf(e: RoundEntry) {
  const m = /^([A-E]) Main · (?:Race (\d+)|(Final))/.exec(e.label ?? '')
  if (!m) return { name: 'Qualifiers', order: 0, no: String(e.round), unit: 'ROUND', key: e.round }
  return { name: `${m[1]} Main`, order: 1 + MAINS.indexOf(m[1]), no: m[3] ? 'F' : m[2], unit: m[3] ? 'FINAL' : 'RACE', key: m[3] ? 1e6 : Number(m[2]) }
}
rounds.rounds.sort((a, b) => { const x = sectionOf(a), y = sectionOf(b); return (x.order - y.order) || (x.key - y.key) })
$('series').textContent = rounds.title
document.title = rounds.title.replace(/\s+—.*$/, '')

const NOTE = 'Flight lines are estimated from each pilot\'s DVR video by image analysis and are approximate, not measured positions.'
const fmt = (s: number) => s >= 60 ? `${Math.floor(s / 60)}:${(s % 60).toFixed(3).padStart(6, '0')}` : s.toFixed(3)
const ordinal = (n: number) => { const s = n % 100 >= 11 && n % 100 <= 13 ? 'TH' : (['TH', 'ST', 'ND', 'RD'][n % 10] ?? 'TH'); return [String(n), s] }
// a country's flag from its ISO-2 code (regional indicator letters; systems without flag emoji show the letters)
const flag = (cc?: string) => { if (!cc || !/^[A-Za-z]{2}$/.test(cc)) return null
  const s = document.createElement('span'); s.className = 'flag'; s.title = cc.toUpperCase()
  s.textContent = String.fromCodePoint(...[...cc.toUpperCase()].map(c => 0x1f1a5 + c.charCodeAt(0))); return s }
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text = '') => { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e }
const hex = (h: string) => new pc.Color(parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255)
const grey = new pc.Color(0.55, 0.57, 0.6)

// ---- one round: everything that depends on race.json, built on load and torn down on a switch ----
type Drone = { p: Pilot; e: pc.Entity; m: pc.StandardMaterial; tag: HTMLDivElement; col: pc.Color; trail: pc.Mesh }
type Round = {
  entry: RoundEntry; race: Race; dir: string; P: Pilot[]; FPS: number; T0: number; T_END: number; root: pc.Entity; drones: Drone[]
  videos: (HTMLVideoElement | null)[]; tiles: (HTMLDivElement | null)[]; speedTags: (HTMLDivElement | null)[]; lapTags: (HTMLDivElement | null)[]; audio: HTMLAudioElement | null; trackPts: pc.Vec3[]; trackS: number[]; trackMesh: pc.Mesh
  gates: { pts: pc.Vec3[]; mesh: pc.Mesh; col: pc.Color }[]; grid: pc.Vec3[]
  box: { min: number[]; max: number[] }; topView: { cx: number; cz: number; cy: number; yaw: number; all: number[][]; fit: null | { key: string; x: number; z: number; h: number } }
  planT: pc.Vec3[]; planD: number[]; NS: number
}
let R: Round | null = null, loadingRound = false, loadToken = 0

// position at clip time t: frames interpolated; before the path starts, its first frame; after its end, its last
function at(p: Pilot, t: number, FPS = R!.FPS): pc.Vec3 {
  const f = Math.max(0, Math.min(p.pos.length - 1, t * FPS)), i = Math.floor(f), j = Math.min(p.pos.length - 1, i + 1), u = f - i
  const a = p.pos[i], b = p.pos[j]; return new pc.Vec3(a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u)
}
// speed at clip time t from the path, km/h: the distance over +-0.1 s
function kmh(p: Pilot, t: number) { if (t > p.end || t < 0.1) return 0; return at(p, Math.min(p.end, t + 0.1)).distance(at(p, t - 0.1)) / 0.2 * 3.6 }
// a pilot is done when it crosses the finish (fixed-lap races) or, in a timed heat with no finish, when its path ends
// without a crash; a crash before that is out
const doneAt = (p: Pilot) => p.finish ?? (p.crashed ? null : p.end)
const lapTime = (p: Pilot, n: number) => p.official_laps?.[n] ?? p.laps[n]
const totalTime = (p: Pilot) => p.official_laps ? p.official_laps.reduce((a, x) => a + x, 0) : p.total
// lap 1 runs from the pilot's own holeshot (its first crossing of the start gate), else from the start signal
const lapStart = (p: Pilot) => p.holeshot_at ?? R!.race.start

async function loadRound(entry: RoundEntry) {
  const token = ++loadToken, wasPlaying = playing; loadingRound = true
  say(`loading round ${entry.round}…`); setPlaying(false)
  const dir = BASE + entry.data.replace(/\/?$/, '/')
  let race: Race
  try { race = await fetchJson<Race>(dir + 'race.json') }
  catch (e) { if (token === loadToken) { loadingRound = false; say(`could not load ${dir}race.json: ${(e as Error).message}`) } return }
  if (token !== loadToken) return                                     // another round was picked meanwhile
  const prevFollow = R && camMode[0] === 'p' ? R.P[Number(camMode.slice(1))]?.name : null
  disposeRound(); mediaAbort = new AbortController()
  // ?scene=<name>: another scan from the same data folder, to compare a retrained one before it replaces the published
  loadScene(/^[A-Za-z0-9._-]+$/.test(QS.get('scene') ?? '') ? QS.get('scene')! : race.scene)
  loadSky(/^[A-Za-z0-9._-]+$/.test(race.sky ?? '') ? race.sky! : 'sky.jpg')
  const P = race.pilots, FPS = race.fps
  const T_END = Math.max(...P.map(p => p.end)), T0 = race.start - 2 - AUDIO_LEAD
  const root = new pc.Entity('round'); app.root.addChild(root)
  const drones: Drone[] = P.map(p => {
    // the dot: a small opaque sphere (0.12 m, as the live page's) in the opaque pass with depth, so the splats in front of
    // it cover it and it sits over the trail's head
    const m = new pc.StandardMaterial(); m.diffuse = new pc.Color(0, 0, 0); m.emissive = hex(p.color); m.useLighting = false; m.depthWrite = true; m.depthTest = true; m.update()
    const e = new pc.Entity(p.name); e.addComponent('render', { type: 'sphere', material: m, castShadows: false }); e.setLocalScale(0.12, 0.12, 0.12); root.addChild(e)
    const tag = el('div', 'tag'); document.body.appendChild(tag)
    return { p, e, m, tag, col: hex(p.color), trail: ribbonMesh(app, root, true, true) }   // opaque, with depth: behind what stands in front of it
  })
  // the pilots' own DVR feeds, played on the same clock; a pilot without a clip has no tile
  const dvr = $('dvr'), tiles: (HTMLDivElement | null)[] = [], speedTags: (HTMLDivElement | null)[] = [], lapTags: (HTMLDivElement | null)[] = []
  const videos = P.map(p => {
    if (!p.video) { tiles.push(null); speedTags.push(null); lapTags.push(null); return null }
    const tile = el('div', 'tile'), [vw, vh] = p.video_size ?? [4, 3]; tile.style.setProperty('--ar', `${vw} / ${vh}`)   // 480x320: the overlay bar cropped off
    const v = el('video'); v.muted = true; v.playsInline = true; v.preload = 'auto'
    whenScanSettled(() => { if (token === loadToken) loadMedia(v, dir + p.video, token) })
    const name = el('span', 'nm'); name.style.borderLeftColor = p.color; const f = flag(p.country); if (f) name.append(f); name.append(p.name)
    const sp = el('div', 'kmh'), lt = el('div', 'lt'); tiles.push(tile); speedTags.push(sp); lapTags.push(lt)
    tile.append(v, name, lt, sp); tile.onclick = () => setMode(camMode === 'p' + P.indexOf(p) ? 'orbit' : 'p' + P.indexOf(p)); dvr.appendChild(tile); return v
  })
  // the broadcast's sound, when the round has one
  const audio = new Audio(); audio.preload = 'auto'; audio.muted = soundMuted
  whenScanSettled(() => { if (token === loadToken) loadMedia(audio, dir + 'audio.m4a', token) })
  audio.addEventListener('loadedmetadata', () => { if (R?.audio === audio) $('soundbtn').hidden = false })
  audio.addEventListener('error', () => { if (R?.audio === audio) { R.audio = null; $('soundbtn').hidden = true } })
  // the track: the closed average line (make_race.py), resampled every 0.25 m; trackS is each point's arc length from
  // the start, which the dashes are measured along (in metres, so they stay the same size on the ground at any zoom)
  const trackPts = (() => {
    const sm = (race.track ?? []).map(q => new pc.Vec3(q[0], q[1], q[2])), n = sm.length; if (n < 3) return [] as pc.Vec3[]
    const out: pc.Vec3[] = []; let carry = 0
    for (let i = 0; i < n; i++) { const a = sm[i], b = sm[(i + 1) % n], L = a.distance(b); if (L < 1e-6) continue; let d = carry; for (; d < L; d += 0.25) out.push(new pc.Vec3().lerp(a, b, d / L)); carry = d - L }
    return out
  })()
  const trackS = trackPts.map((q, k) => k ? q.distance(trackPts[k - 1]) : 0); for (let k = 1; k < trackS.length; k++) trackS[k] += trackS[k - 1]
  const all = P.flatMap(p => p.pos), m = 10
  const box = { min: [0, 1, 2].map(k => Math.min(...all.map(q => q[k])) - m), max: [0, 1, 2].map(k => Math.max(...all.map(q => q[k])) + (k === 1 ? 40 : m)) }
  const topView = (() => {
    const cx = all.reduce((a, q) => a + q[0], 0) / all.length, cz = all.reduce((a, q) => a + q[2], 0) / all.length
    // laid out as MultiGP's official track map: the start pads bottom-left, the speed-trap straight along the bottom
    // running left to right into the start / finish gate, the rest of the course above. In the page's frame that is screen-right = world -z and screen-up = world -x (right x up = +y, so
    // nothing is mirrored); the pads -> gate line then runs right along the bottom
    const right = new pc.Vec3(0, 0, -1), down = new pc.Vec3(1, 0, 0)
    // the yaw whose screen-right and screen-up best match (searched rather than derived: a closed form was 30 deg out)
    let yaw = 0, best = -9
    for (let y = 0; y < 360; y += 0.5) { const r = new pc.Quat().setFromEulerAngles(-89.9, y, 0), rt = r.transformVector(new pc.Vec3(1, 0, 0), new pc.Vec3()), up = r.transformVector(new pc.Vec3(0, 1, 0), new pc.Vec3())
      const c = rt.dot(right) - up.dot(down); if (c > best) { best = c; yaw = y } }
    // cy: the course's ground level - the track line's median height (the lowest path point is a crash below the ground)
    const ys = (race.track ?? []).map(q => q[1]).sort((a, b) => a - b), cy = ys.length ? ys[ys.length >> 1] : Math.min(...all.map(q => q[1]))
    return { cx, cz, cy, yaw, all, fit: null as null | { key: string; x: number; z: number; h: number } }
  })()
  // the gates: each opening's frame, 0.1 m apart along it so the ribbon turns only at the corners; the start / finish gate in red.
  // Drawn only while there is no scan (the update loop checks sceneMissing every frame, since the scan loads after the
  // round is built and may fail later): the scan shows the real gates, and the frames are gates_from_map.py's estimate, which is
  // not ground truth
  const gates = (race.checkpoints ?? (race.gate ? [race.gate] : [])).filter(g => g.half_width && g.height && g.width_dir).map(g => {
    const c = new pc.Vec3(g.centre[0], g.centre[1], g.centre[2]), w = new pc.Vec3(g.width_dir![0], g.width_dir![1], g.width_dir![2]).mulScalar(g.half_width!), up = new pc.Vec3(0, g.height!, 0)
    const corners = [c.clone().sub(w), c.clone().sub(w).add(up), c.clone().add(w).add(up), c.clone().add(w)], pts: pc.Vec3[] = []
    corners.forEach((a, k) => { const b = corners[(k + 1) % 4], n = Math.max(1, Math.round(a.distance(b) / 0.1)); for (let i = 0; i < n; i++) pts.push(new pc.Vec3().lerp(a, b, i / n)) })
    return { pts, mesh: ribbonMesh(app, root), col: g.start || g.finish ? hex('#e1262c') : hex('#030a18') }
  })
  // a ground grid every 10 m, drawn only while there is no scan
  const grid: pc.Vec3[] = [], gy = topView.cy - 0.05, g0 = box.min.map(v => Math.floor(v / 10) * 10), g1 = box.max.map(v => Math.ceil(v / 10) * 10)
  for (let x = g0[0]; x <= g1[0]; x += 10) grid.push(new pc.Vec3(x, gy, g0[2]), new pc.Vec3(x, gy, g1[2]))
  for (let z = g0[2]; z <= g1[2]; z += 10) grid.push(new pc.Vec3(g0[0], gy, z), new pc.Vec3(g1[0], gy, z))
  R = { entry, race, dir, P, FPS, T0, T_END, root, drones, videos, tiles, speedTags, lapTags, audio, trackPts, trackS, trackMesh: ribbonMesh(app, root), gates, grid, box, topView, planT: [], planD: [], NS: Math.ceil((T_END - T0) / DT) + 1 }
  t = T0
  // the camera: following a pilot carries over to the same pilot in the new round, otherwise the orbit
  if (camMode[0] === 'p') { const k = prevFollow ? P.findIndex(p => p.name === prevFollow) : -1; camMode = k >= 0 ? 'p' + k : 'orbit'; lastMode = camMode }
  if (camMode === 'orbit') view.yawOffset = 30
  startBlend(); refreshCam()
  showRoundHeader(entry); buildBoard(); buildResults()
  sizeTimeline(); measureFree(); plan()
  loadingRound = false; say(sceneNote())
  setPlaying(wasPlaying)
}
function disposeRound() {
  if (!R) return
  mediaAbort?.abort(); mediaAbort = null
  for (const v of [...R.videos, R.audio]) if (v) { v.pause(); if (v.src.startsWith('blob:')) URL.revokeObjectURL(v.src); v.removeAttribute('src'); v.load() }
  for (const d of R.drones) d.tag.remove()
  $('dvr').replaceChildren(); R.root.destroy(); R = null
}

// ---- the round header and menu ----
const roundIdx = () => R ? rounds.rounds.indexOf(R.entry) : -1
function showRoundHeader(e: RoundEntry) {
  $('rname').textContent = e.label || `Qualifier Round ${e.round}`
  const n = (e.pilots ?? R!.P.map(p => p.name)).length
  const info = el('span', 'info', 'i'); info.title = NOTE; info.setAttribute('aria-label', NOTE)
  $('rmeta').replaceChildren([e.completed, `${n} pilot${n === 1 ? '' : 's'}`, e.fastest ? `fastest ${e.fastest.pilot} ${e.fastest.lap.toFixed(3)}` : ''].filter(Boolean).join(' · '), info)
  const i = roundIdx(); $<HTMLButtonElement>('prevbtn').disabled = i <= 0; $<HTMLButtonElement>('nextbtn').disabled = i >= rounds.rounds.length - 1
  document.querySelectorAll('.ritem').forEach(b => b.classList.toggle('on', (b as HTMLElement).dataset.round === String(e.round)))
}
function pick(e: RoundEntry, push = true) {
  closeRounds()
  if (R?.entry === e) return
  if (push) { const q = new URLSearchParams(location.search); q.set('round', String(e.round)); history.pushState(null, '', '?' + q.toString()) }
  loadRound(e).catch((err: Error) => { loadingRound = false; say(`could not show round ${e.round}: ${err.message}`) })
}
const rlist = $('rlist'), rfilter = $<HTMLInputElement>('rfilter'), roundsBox = $('rounds')
function buildRoundList() {
  const q = rfilter.value.trim().toUpperCase()
  const items = rounds.rounds.filter(e => !q || String(e.round) === q.replace(/^R(OUND)?\s*/, '') || (e.pilots ?? []).some(n => n.toUpperCase().includes(q)) || (e.label ?? '').toUpperCase().includes(q))
  let section = ''
  rlist.replaceChildren(...items.flatMap(e => {
    const sec = sectionOf(e), b = el('button', 'ritem' + (R?.entry === e ? ' on' : '')); b.dataset.round = String(e.round)
    const no = el('div', 'rno', sec.no); no.prepend(el('small', '', sec.unit))
    const mid = el('div'); mid.append(el('div', 'rwhen', e.completed ?? ''), el('div', 'rpl', (e.pilots ?? []).join(' · ')))
    const fast = el('div', 'rfast'); if (e.fastest) { fast.append('fastest lap', el('b', '', e.fastest.lap.toFixed(3)), e.fastest.pilot) }
    b.append(no, mid, fast); b.onclick = () => pick(e)
    if (sec.name === section) return [b]
    section = sec.name; return [el('div', 'rsec', sec.name), b]
  }))
  if (!items.length) { const none = el('div', '', 'No round matches.'); none.id = 'rempty'; rlist.append(none) }
}
function openRounds() { roundsBox.hidden = false; rfilter.value = ''; buildRoundList(); (rlist.querySelector('.on') as HTMLElement | null)?.scrollIntoView({ block: 'center' }); if (!phone()) rfilter.focus() }
function closeRounds() { roundsBox.hidden = true }
$('roundbtn').onclick = () => roundsBox.hidden ? openRounds() : closeRounds()
$('rclose').onclick = closeRounds
rfilter.oninput = buildRoundList
rfilter.onkeydown = e => { if (e.key === 'Enter') { const first = rlist.querySelector('.ritem') as HTMLButtonElement | null; first?.click() } }
const step = (d: number) => { const i = roundIdx() + d; if (i >= 0 && i < rounds.rounds.length) pick(rounds.rounds[i]) }
$('prevbtn').onclick = () => step(-1); $('nextbtn').onclick = () => step(1)
window.addEventListener('popstate', () => { const e = roundFromUrl(); if (e) pick(e, false) })
function roundFromUrl() { const n = Number(new URLSearchParams(location.search).get('round')); return rounds.rounds.find(e => e.round === n) ?? null }

// ---- the standings, broadcast style ----
// live: laps done, then who crossed the line earlier; once every path has ended, the official place where there is one
function standings(tc: number) {
  const P = R!.P, over = P.every(p => tc > p.end)
  const rows = P.map(p => {
    const done = p.lap_ends.filter(x => x <= tc).length, d = doneAt(p), finished = d !== null && tc >= d, out = !finished && tc > p.end
    return { p, done, finished, out, last: done ? p.lap_ends[done - 1] : Infinity }
  })
  rows.sort((a, b) => (b.done - a.done) || (a.last - b.last) || (Number(a.out) - Number(b.out)))
  if (over && P.some(p => p.position)) rows.sort((a, b) => (a.p.position ?? 99) - (b.p.position ?? 99))
  return rows
}
type BoardRow = { el: HTMLButtonElement; place: HTMLElement; lap: HTMLElement; lapLabel: HTMLElement; lapTime: HTMLElement }
let boardRows: BoardRow[] = []
function buildBoard() {
  const board = $('board')
  boardRows = R!.P.map((p, k) => {
    const b = el('button', 'lb') as HTMLButtonElement; b.setAttribute('role', 'listitem'); b.title = `follow ${p.name}`
    const place = el('div', 'place'), bar = el('div', 'bar'); bar.style.setProperty('--c', p.color)
    const f = flag(p.country); if (f) bar.append(f)
    bar.append(el('span', 'nm', p.name))
    const lap = el('div', 'lap'), lapLabel = el('i'), lapTime = el('b'); lap.append(lapLabel, lapTime)
    b.append(place, bar, lap); b.onclick = () => { setMode(camMode === 'p' + k ? 'orbit' : 'p' + k); b.blur() }
    return { el: b, place, lap, lapLabel, lapTime }
  })
  board.replaceChildren(...boardRows.map(r => r.el)); lastBoard.length = 0
}
const lastBoard: string[] = []
function drawBoard(tc: number) {
  const { P, race } = R!, rt = tc - race.start, st = standings(tc), board = $('board'), tileOrder: number[] = []
  st.forEach((s, k) => {
    const i = P.indexOf(s.p), r = boardRows[i]; let label: string, time: string, flash = false
    if (s.finished) { label = s.p.finish !== null ? 'FINISH' : `${s.done} LAP${s.done === 1 ? '' : 'S'}`; time = s.p.finish !== null ? fmt(totalTime(s.p)) : s.done ? fmt(Math.min(...s.p.laps.map((_, n) => lapTime(s.p, n)))) : '' }
    else if (s.out) { label = 'OUT'; time = s.done ? `${s.done} LAP${s.done === 1 ? '' : 'S'}` : '' }
    else if (rt < 0) { label = 'READY'; time = '' }
    else if (!s.done && tc < lapStart(s.p)) { label = 'START'; time = '' }
    else if (s.done && tc - s.last < FLASH) { label = `LAP ${s.done}`; time = fmt(lapTime(s.p, s.done - 1)); flash = true }
    else { label = `LAP ${s.done + 1}`; time = fmt(Math.min(tc, s.p.end) - (s.done ? s.last : lapStart(s.p))) }
    const [n, suf] = ordinal(k + 1), key = `${k}|${label}|${time}|${flash}|${s.out}|${camMode}`
    if (lastBoard[i] !== key) {   // the DOM only changes when the row does
      lastBoard[i] = key
      r.place.replaceChildren(n, el('sup', '', suf)); r.lapLabel.textContent = label; r.lapTime.textContent = time
      r.lap.classList.toggle('flash', flash); r.el.classList.toggle('out', s.out); r.el.classList.toggle('sel', camMode === 'p' + i)
    }
    if (board.children[k] !== r.el) board.insertBefore(r.el, board.children[k] ?? null)
    tileOrder.push(i)
    const st_ = R!.speedTags[i]; if (st_) { const v = s.out || tc > s.p.end ? 0 : kmh(s.p, tc); st_.textContent = v ? `${v.toFixed(0).padStart(3)} km/h` : '' }
    const lt = R!.lapTags[i]; if (lt) { const txt = time ? `${label} ${time}` : label; if (lt.textContent !== txt) lt.textContent = txt; lt.classList.toggle('flash', flash) }
  })
  // the DVR tiles in the same order, the followed pilot's first
  const dvr = $('dvr'), fk = camMode[0] === 'p' ? Number(camMode.slice(1)) : -1
  const tiles = (fk >= 0 ? [fk, ...tileOrder.filter(i => i !== fk)] : tileOrder).map(i => R!.tiles[i]).filter((t): t is HTMLDivElement => t !== null)
  tiles.forEach((t, k) => { if (dvr.children[k] !== t) dvr.insertBefore(t, dvr.children[k] ?? null) })
  const unit = el('small', '', 's')
  $('clock').replaceChildren(document.createTextNode(rt < 0 ? '−' + (-rt).toFixed(3) : rt.toFixed(3)), unit)
}

// ---- official results (the extras: country, official_laps, position), or the estimate where there are none ----
function buildResults() {
  const { P } = R!, official = P.some(p => p.official_laps || p.position)
  const order = [...P].sort((a, b) => (a.position ?? 99) - (b.position ?? 99) || (b.laps.length - a.laps.length) || (a.total - b.total))
  const tbl = el('table'), head = el('tr'); for (const h of ['Pos', 'Pilot', 'Laps', 'Total', 'Best', 'Lap times']) head.append(el('th', '', h)); tbl.append(head)
  for (const p of order) {
    const times = p.official_laps ?? p.laps, best = times.length ? Math.min(...times) : null
    const tr = el('tr'), who = el('td', 'who'), dot = el('i'); dot.style.background = p.color; who.append(dot)
    const f = flag(p.country); if (f) { who.append(f, ' ') } who.append(p.name)
    const laps = el('td', 'laps'); times.forEach((x, n) => { if (n) laps.append(' · '); laps.append(x === best ? el('b', '', x.toFixed(3)) : x.toFixed(3)) })
    tr.append(el('td', 'pos', p.position ? String(p.position) : '–'), who, el('td', 'num', String(times.length)), el('td', 'num', times.length ? fmt(totalTime(p)) : '–'), el('td', 'num', best !== null ? best.toFixed(3) : '–'), laps)
    tbl.append(tr)
  }
  const src = el('div', 'src', (official ? 'Lap times and places: official LiveFPV timing. ' : 'No official timing in this round: lap times are estimated from the DVR paths. ') + NOTE)
  $('restable').replaceChildren(tbl, src)
}
const resBox = $('results'), resBtn = $<HTMLButtonElement>('resbtn'); resBtn.hidden = false
const setResults = (v: boolean) => { resBox.hidden = !v; resBtn.classList.toggle('on', v); resBtn.setAttribute('aria-pressed', String(v)) }
resBtn.onclick = () => { setResults(resBox.hidden); resBtn.blur() }
$('resclose').onclick = () => setResults(false)

// ---- playback ----
let t = 0, playing = false, speed = 1, soundMuted = false
const play = $<HTMLButtonElement>('play')
function setPlaying(v: boolean) { playing = v; play.textContent = v ? '❚❚ Pause' : '▶ Play' }
play.onclick = () => { if (!R) return; if (t >= R.T_END - 0.01) t = R.T0; setPlaying(!playing) }
const speedSel = $<HTMLSelectElement>('speed'); speedSel.onchange = () => { speed = Number(speedSel.value); refreshSpeed() }
const refreshSpeed = segment($('speedseg'), speedSel, o => [document.createTextNode(o.text + '×')]); refreshSpeed()
const soundBtn = $<HTMLButtonElement>('soundbtn')
soundBtn.onclick = () => { soundMuted = !soundMuted; if (R?.audio) R.audio.muted = soundMuted; $('snd-on').toggleAttribute('hidden', soundMuted); $('snd-off').toggleAttribute('hidden', !soundMuted); soundBtn.setAttribute('aria-pressed', String(!soundMuted)); soundBtn.blur() }
const mediaTime = (v: HTMLMediaElement) => v === R!.audio ? t + AUDIO_LEAD : Math.max(0, t)   // the audio file starts AUDIO_LEAD s early
function syncVideos() {
  for (const v of [...R!.videos, R!.audio]) {
    if (!v) continue
    const mt = mediaTime(v), run = playing && !seeking && (v === R!.audio || t >= 0)   // videos hold their first frame before the clip
    if (run) {
      v.playbackRate = speed; if (v.paused) v.play().catch(() => {})
      if (Math.abs(v.currentTime - mt) > 0.15 && !v.seeking) v.currentTime = mt
    } else {
      if (!v.paused) v.pause()
      if (Math.abs(v.currentTime - mt) > 0.02 && !v.seeking) v.currentTime = mt
    }
  }
}
window.addEventListener('keydown', e => {
  if (e.target instanceof HTMLInputElement) { if (e.key === 'Escape') closeRounds(); return }
  if (e.key === 'Escape') { closeRounds(); setResults(false); return }
  if (!R) return
  if (e.key === ' ') { play.click(); e.preventDefault() }
  if (e.key === 'ArrowRight') t = Math.min(R.T_END, t + (e.shiftKey ? 5 : 1)); if (e.key === 'ArrowLeft') t = Math.max(R.T0, t - (e.shiftKey ? 5 : 1))
  if (e.key === '[') step(-1); if (e.key === ']') step(1)
  const k = Number(e.key); if (k >= 1 && k <= 3) setMode(BASE_MODES[k - 1][0]); else if (k >= 4 && k - 4 < R.P.length) setMode('p' + (k - 4))
})

// ---- the timeline: one row per pilot with its lap ends, the playhead; drag to seek ----
const tl = $<HTMLCanvasElement>('tl'), TL_NAME = 92, TL_ROW = () => phone() ? 13 : 17
function sizeTimeline() { tl.style.height = (R!.P.length * TL_ROW() + 4) + 'px' }
function drawTimeline(tc: number) {
  const { P, race, T0, T_END } = R!
  const r = tl.getBoundingClientRect(), dpr = devicePixelRatio, W = Math.round(r.width * dpr), H = Math.round(r.height * dpr)
  if (tl.width !== W || tl.height !== H) { tl.width = W; tl.height = H }
  const g = tl.getContext('2d')!; g.clearRect(0, 0, W, H)
  const nameW = phone() ? 0 : TL_NAME * dpr, L = nameW + 4 * dpr, R_ = W - 8 * dpr, x = (tt: number) => L + (tt - T0) / (T_END - T0) * (R_ - L), rowH = (H - 4 * dpr) / P.length
  g.textBaseline = 'middle'
  P.forEach((p, k) => {
    const y = rowH * (k + 0.5) + 2 * dpr, out = p.crashed && tc > p.end
    if (nameW) { g.fillStyle = p.color; g.fillRect(0, y - 4 * dpr, 3 * dpr, 8 * dpr)
      g.font = `700 ${11 * dpr}px "Roboto Condensed", sans-serif`; g.fillStyle = out ? '#8c9199' : '#030a18'
      let s = p.name; while (s.length > 2 && g.measureText(s).width > nameW - 10 * dpr) s = s.slice(0, -1); g.fillText(s === p.name ? s : s + '…', 7 * dpr, y) }
    g.fillStyle = '#e1e3e8'; g.fillRect(L, y - 2 * dpr, R_ - L, 4 * dpr)
    g.fillStyle = p.color; g.globalAlpha = 0.45; g.fillRect(x(race.start), y - 2 * dpr, x(Math.min(p.end, T_END)) - x(race.start), 4 * dpr); g.globalAlpha = 1
    if (p.holeshot_at !== undefined) { g.fillStyle = '#5d6472'; g.fillRect(x(p.holeshot_at) - 0.5 * dpr, y - rowH * 0.3, 1 * dpr, rowH * 0.6) }   // the holeshot: lap 1 starts here
    g.font = `500 ${10 * dpr}px "Roboto Mono", monospace`
    for (const [n, e] of p.lap_ends.entries()) {
      g.fillStyle = '#ab0e0e'; g.fillRect(x(e) - 1 * dpr, y - rowH * 0.42, 2 * dpr, rowH * 0.84)
      const label = lapTime(p, n).toFixed(2), lx = x(e) + 3 * dpr, nx = n + 1 < p.lap_ends.length ? x(p.lap_ends[n + 1]) : R_
      if (g.measureText(label).width + 6 * dpr < nx - lx && rowH >= 15 * dpr) { g.fillStyle = '#5d6472'; g.fillText(label, lx, y - rowH * 0.25) }
    }
    if (p.crashed) { g.fillStyle = '#5d6472'; g.font = `700 ${11 * dpr}px sans-serif`; g.fillText('✕', x(p.end) - 4 * dpr, y) }
  })
  g.strokeStyle = '#030a18'; g.lineWidth = 1 * dpr; g.setLineDash([4 * dpr, 4 * dpr]); g.beginPath(); g.moveTo(x(race.start), 0); g.lineTo(x(race.start), H); g.stroke(); g.setLineDash([])
  g.fillStyle = '#ab0e0e'; g.fillRect(x(tc) - 1 * dpr, 0, 2 * dpr, H)
}
let seeking = false
const seek = (e: PointerEvent) => { if (!R) return; const r = tl.getBoundingClientRect(); const L = (phone() ? 0 : TL_NAME) + 4, Rr = r.width - 8
  t = Math.max(R.T0, Math.min(R.T_END, R.T0 + (e.clientX - r.left - L) / (Rr - L) * (R.T_END - R.T0))) }
tl.addEventListener('pointerdown', e => { seeking = true; tl.setPointerCapture(e.pointerId); seek(e) })
tl.addEventListener('pointermove', e => { if (seeking) seek(e) })
const endSeek = () => { seeking = false }
tl.addEventListener('pointerup', endSeek); tl.addEventListener('pointercancel', endSeek); tl.addEventListener('lostpointercapture', endSeek); window.addEventListener('pointerup', endSeek)

// ---- toggles ----
let showTrack = true
const trackBtn = $<HTMLButtonElement>('trackbtn')
trackBtn.onclick = () => { showTrack = !showTrack; trackBtn.classList.toggle('on', showTrack); trackBtn.setAttribute('aria-pressed', String(showTrack)); trackBtn.blur() }
const dvrBtn = $<HTMLButtonElement>('dvrbtn')
const setDvr = (v: boolean) => { $('dvr').hidden = !v; document.body.classList.toggle('withdvr', v); dvrBtn.classList.toggle('on', v); dvrBtn.setAttribute('aria-pressed', String(v)) }
dvrBtn.onclick = () => { setDvr($('dvr').hidden); dvrBtn.blur() }
setDvr(!narrow())                                                    // the strip is on by default where it has a column of its own
const tlBtn = $<HTMLButtonElement>('tlbtn')
const setTimeline = (v: boolean) => { tl.hidden = !v; tlBtn.classList.toggle('on', v); tlBtn.setAttribute('aria-pressed', String(v)) }
tlBtn.onclick = () => { setTimeline(tl.hidden); tlBtn.blur() }
setTimeline(false)                                                   // collapsed at every width; the Timeline toggle opens it

// ---- camera (the race page's: orbit, top, free, follow one pilot) ----
const FOV = 50, PITCH0 = -18, TURN = 6, DT = 0.1                       // vertical fov of the free area; degrees a race second
const view = { yawOffset: 30, topYaw: 0, pitch: PITCH0, zoom: 1 }
const free = { x: 0, y: 0, w: 1, h: 1, W: 1, H: 1 }                    // the free rect in CSS px, and the canvas size
const stBox = { w: 0, h: 0 }                                         // the standings overlay in the canvas's top-left corner (the fit keeps the pilots out of it)
let stripW = -1, canvasW = 0, canvasH = 0
function measureFree() {
  // the canvas is the window minus the DVR column (its width follows the tiles), full height; the free rect is the
  // canvas above the floating bar (under 1000 px, above the DVR row too). The standings overlay is not cut out of it -
  // that pushed the view's centre to the right of where the eye expects - but the fit keeps the pilots out from under it;
  // on a phone the standings run across the top, so there the free rect starts below them
  const W = innerWidth, H = innerHeight, dvr = $('dvr'), left = $('left').getBoundingClientRect()
  const column = getComputedStyle(dvr).display !== 'none' && !narrow()
  const sw = column ? Math.round(dvr.getBoundingClientRect().width) : 0
  if (sw !== stripW) { stripW = sw; document.documentElement.style.setProperty('--stripw', sw + 'px') }
  const cw = Math.max(100, W - sw), ch = H
  if (cw !== canvasW || ch !== canvasH) { canvasW = cw; canvasH = ch; app.resizeCanvas(cw, ch) }
  const bt = Math.round($('bottom').getBoundingClientRect().top)
  const y = phone() ? Math.min(ch - 100, Math.round(left.bottom)) : 0
  const next = { x: 0, y, w: cw, h: Math.max(100, Math.min(bt, ch) - y), W: cw, H: ch }
  const sw_ = phone() ? 0 : Math.round(left.right), sh_ = phone() ? 0 : Math.round(left.bottom)
  const changed = (Object.keys(next) as (keyof typeof free)[]).some(k => next[k] !== free[k]) || sw_ !== stBox.w || sh_ !== stBox.h
  Object.assign(free, next); stBox.w = sw_; stBox.h = sh_; return changed
}
// off-centre projection: the free rect's centre is the optical axis and its height spans FOV. The top view is centred
// on the whole canvas instead (its course sat high under the standings otherwise); its fit is still scaled to the free rect
cam.camera!.calculateProjection = (m: pc.Mat4) => {
  const near = cam.camera!.nearClip, far = cam.camera!.farClip, s_ = 2 * near * Math.tan(FOV * Math.PI / 360) / free.h
  const top = camMode === 'top', cx = top ? free.W / 2 : free.x + free.w / 2, cy = top ? free.H / 2 : free.y + free.h / 2
  m.setFrustum((0 - cx) * s_, (free.W - cx) * s_, (cy - free.H) * s_, (cy - 0) * s_, near, far)
}
const BASE_MODES: [string, string][] = [['orbit', 'Orbit'], ['top', 'Top'], ['free', 'Free']]
let camMode = 'orbit', lastMode = 'orbit'
const camBtns = BASE_MODES.map(([v, label]) => { const b = el('button', '', label); b.onclick = () => { setMode(v); b.blur() }; return b })
$('camseg').replaceChildren(...camBtns)
function refreshCam() {
  camBtns.forEach((b, i) => b.classList.toggle('on', BASE_MODES[i][0] === camMode))
  const k = camMode[0] === 'p' ? Number(camMode.slice(1)) : -1
  if (R) for (const [i, d] of R.drones.entries()) { d.tag.style.zIndex = i === k ? '2' : '1'; R.tiles[i]?.classList.toggle('sel', i === k) }
  layoutStrip()
}
// The tiles are sized here, not in CSS: one column of equal tiles filling the window's height exactly (tile height =
// the height over the count, 2 px apart; width from the clips' shape). Their order is the standings', the followed
// pilot's first (drawBoard).
function layoutStrip() {
  const dvr = $('dvr'), n = R?.tiles.filter(Boolean).length ?? 0
  const ah = Math.max(0.5, ...(R?.P.filter(p => p.video).map(p => p.video_size ? p.video_size[1] / p.video_size[0] : 0.75) ?? [0.75]))   // the tallest clip shape sets the fit
  dvr.style.setProperty('--ah', ah.toFixed(4)); dvr.style.setProperty('--rows', String(Math.max(1, n)))
  if (!n || narrow()) { dvr.style.removeProperty('--tilew'); return }   // the row above the bar: CSS sizes
  const gap = 2, h = (innerHeight - (n - 1) * gap) / n
  const w = Math.min(h / ah, innerWidth * 0.25)                         // a round with one or two pilots would make the column most of the window
  dvr.style.setProperty('--tilew', (Math.floor(w * 2) / 2).toFixed(1) + 'px')   // rounded down to a half pixel: a hair of overflow would add a scrollbar
}
const yawAt = (tc: number) => view.yawOffset + (camMode === 'orbit' ? TURN * (tc - R!.T0) : 0)
const gauss = (xs: number[], sigma: number) => { const r = Math.ceil(3 * sigma / DT), w = Array.from({ length: 2 * r + 1 }, (_, k) => Math.exp(-0.5 * ((k - r) * DT / sigma) ** 2))
  return xs.map((_, i) => { let a = 0, b = 0; for (let k = -r; k <= r; k++) { const j = Math.min(xs.length - 1, Math.max(0, i + k)); a += xs[j] * w[k + r]; b += w[k + r] } return a / b }) }
// the orbit, worked out for the whole round up front: the target is the drones' middle smoothed both ways, and the
// distance the least that holds all of them inside the free part of the screen, kept within the scanned course
function plan() {
  if (!R) return
  const { P, T0, NS, box } = R
  const tanV0 = Math.tan(FOV * Math.PI / 360) * 0.96, tanV = tanV0 * 0.85 / 0.96, tanH = tanV * free.w / free.h, tanH0 = tanV0 * free.w / free.h   // the fit aims 15% inside the free rect; 2% of its width / height inside the edge is the hard limit
  const inFrame = (p: Pilot, tc: number) => !(tc > p.end && doneAt(p) === null)       // a pilot who went out drops out of the frame
  const mids = Array.from({ length: NS }, (_, i) => { const tc = T0 + i * DT; const q = P.filter(p => inFrame(p, tc)); return (q.length ? q : P).map(p => at(p, Math.min(tc, p.end))) })
  const avg = (k: 'x' | 'y' | 'z') => gauss(mids.map(q => q.reduce((s_, v) => s_ + v[k], 0) / q.length), 1.5)   // the look-at centre, smoothed over +-1.5 s (the fit below is measured from this centre, so its lag costs no one the frame)
  const cx = avg('x'), cy = avg('y'), cz = avg('z')
  const planT = cx.map((x, i) => new pc.Vec3(x, cy[i], cz[i]))
  const need: number[] = [], hard: number[] = [], room: number[] = []
  // the standings box's right edge and bottom in the hard frame's units (-1..1 across the free rect)
  const cornerX = (-1 + 2 * stBox.w / free.w) / 0.96, cornerY = (1 - 2 * stBox.h / free.h) / 0.96
  for (let i = 0; i < NS; i++) {
    const r = new pc.Quat().setFromEulerAngles(view.pitch, yawAt(T0 + i * DT), 0)
    const dir = r.transformVector(new pc.Vec3(0, 0, 1), new pc.Vec3()), right = r.transformVector(new pc.Vec3(1, 0, 0), new pc.Vec3()), up = r.transformVector(new pc.Vec3(0, 1, 0), new pc.Vec3())
    let d = Math.max(12, dir.y > 1e-3 ? (box.min[1] + 10 + 8 - planT[i].y) / dir.y : 12)   // never lower than 8 m over the ground
    let h = d
    for (const q of mids[i]) { const v = q.clone().sub(planT[i]), a = v.dot(right), b = v.dot(up), c = v.dot(dir)
      d = Math.max(d, Math.abs(a) / tanH + c, Math.abs(b) / tanV + c); h = Math.max(h, Math.abs(a) / tanH0 + c, Math.abs(b) / tanV0 + c)
      // a pilot headed for the standings' corner (upper left): far enough that it clears the box's right edge or its bottom, whichever is nearer
      if (a < 0 && b > 0 && cornerX < 0 && cornerY > 0) d = Math.max(d, c + Math.min(-a / (tanH0 * -cornerX), b / (tanV0 * cornerY))) }
    need.push(d * view.zoom); hard.push(h * view.zoom)
    let rm = 1e9; const o = [planT[i].x, planT[i].y, planT[i].z], dv = [dir.x, dir.y, dir.z]
    for (let k = 0; k < 3; k++) { if (dv[k] > 1e-6) rm = Math.min(rm, (box.max[k] - o[k]) / dv[k]); else if (dv[k] < -1e-6) rm = Math.min(rm, (box.min[k] - o[k]) / dv[k]) }
    room.push(rm)
  }
  // the fit distance follows its target slowly, so the zoom does not breathe with every wobble of the field: a 3% dead
  // band on the target, then a first-order lag of 2.5 s when closing in and 1 s when backing off. The target is the need
  // over the coming two seconds (the plan is known ahead), so backing off starts before anyone reaches the edge, and the
  // 15% margin is the slack the lag may use: the result is never less than what keeps everyone inside the edge itself
  // (a residual step where even that was not enough: a launch)
  // the target is also kept within the scanned course (room), unless holding everyone in the free rect needs more: a
  // narrow free rect (a wide DVR strip) and a spread-out field put the camera past the course rather than the pilots
  // off screen. Clamped before the smoothing: the room moves with the orbit's heading, and clamping after it made the
  // distance follow every wobble of the course boundary
  const want = need.map((d, i) => Math.min(d, Math.max(room[i], hard[i])))
  const lead = Math.round(2 / DT), ahead = want.map((_, i) => Math.max(...want.slice(i, i + lead + 1)))
  const aIn = 1 - Math.exp(-DT / 2.5), aOut = 1 - Math.exp(-DT / 1.0)
  let held = ahead[0], cur = ahead[0]; const sm: number[] = []
  for (let i = 0; i < NS; i++) { if (Math.abs(ahead[i] - held) > 0.03 * held) held = ahead[i]; cur += (held - cur) * (held > cur ? aOut : aIn); cur = Math.max(cur, hard[i]); sm.push(cur) }   // a forced step is kept as the state, so the way back from it is the slow one
  R.planT = planT; R.planD = gauss(sm, 0.3).map((x, i) => Math.max(6, x, hard[i]))
}
const freeCam = { pivot: new pc.Vec3(), yaw: 0, pitch: -30, dist: 40 }
function enterFree() {
  const fwd = cam.forward.clone(), up = cam.up.clone(), eye = cam.getPosition(), dy = (R?.topView.cy ?? 0) - eye.y
  freeCam.dist = fwd.y < -0.05 && dy < 0 ? Math.max(5, Math.min(200, dy / fwd.y)) : 30
  freeCam.pivot.copy(eye).add(fwd.clone().mulScalar(freeCam.dist))
  const h = fwd.y < -0.99 ? up : fwd.y > 0.99 ? up.mulScalar(-1) : fwd
  freeCam.yaw = Math.atan2(-h.x, -h.z) * 180 / Math.PI; freeCam.pitch = Math.max(-89, Math.min(89, Math.asin(Math.max(-1, Math.min(1, fwd.y))) * 180 / Math.PI))
}
function cameraFree() {
  const r = new pc.Quat().setFromEulerAngles(freeCam.pitch, freeCam.yaw, 0)
  cam.setPosition(r.transformVector(new pc.Vec3(0, 0, freeCam.dist), new pc.Vec3()).add(freeCam.pivot)); cam.setRotation(r)
}
function leaveTop() { if (lastMode === 'top') { cam.camera!.nearClip = 0.2; cam.camera!.farClip = 2000; setTopCut(NaN) } }   // the top view's clipping and cut
function setMode(v: string) {
  if (v === lastMode || !R) return
  const yaw = view.yawOffset + (lastMode === 'orbit' ? TURN * (t - R.T0) : 0)   // the orbit's heading now, kept across the switch
  leaveTop()
  camMode = v; startBlend(); if (v === 'free') enterFree()
  view.yawOffset = v === 'orbit' ? yaw - TURN * (t - R.T0) : yaw; lastMode = v; refreshCam(); plan()
}
let drag: { x: number; y: number } | null = null
canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId); closeRounds() })
canvas.addEventListener('pointerup', () => { drag = null })
canvas.addEventListener('pointermove', e => { if (!drag) return
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag = { x: e.clientX, y: e.clientY }
  if (camMode === 'free') {
    if (e.buttons & 2 || e.shiftKey) { const r = new pc.Quat().setFromEulerAngles(freeCam.pitch, freeCam.yaw, 0), k = freeCam.dist * 0.0015
      freeCam.pivot.sub(r.transformVector(new pc.Vec3(dx * k, 0, 0), new pc.Vec3())).add(r.transformVector(new pc.Vec3(0, dy * k, 0), new pc.Vec3())) }
    else { freeCam.yaw -= dx * 0.3; freeCam.pitch = Math.max(-89, Math.min(89, freeCam.pitch - dy * 0.3)) }
    return
  }
  if (camMode === 'top') { view.topYaw -= dx * 0.3; return }
  view.yawOffset -= dx * 0.3; view.pitch = Math.max(-85, Math.min(-5, view.pitch - dy * 0.3)); plan() })
canvas.addEventListener('contextmenu', e => e.preventDefault())
canvas.addEventListener('wheel', e => { if (camMode === 'free') { freeCam.dist = Math.max(2, Math.min(400, freeCam.dist * Math.exp(e.deltaY * 0.001))); e.preventDefault(); return } view.zoom = Math.max(0.4, Math.min(3, view.zoom * Math.exp(e.deltaY * 0.001))); plan(); e.preventDefault() }, { passive: false })
// the whole course - the track line, the measured gates and every path - fitted into the free rect with a 4% margin:
// a static fit (nothing breathes), worked out once per heading and window shape. Everything above TOP_CUT over the
// ground is cut away: the scan's sky dome and the big far splats above the course hazed the view from above. The near
// plane alone does not cut them (a splat's quad reaches far past its centre), so the splats themselves are dropped in
// the work buffer (scale 0 above the cut, Top mode only); the far plane stops just under the ground
const TOP_CUT = 12, TOP_BELOW = 3
const TOP_CUT_GLSL = /* glsl */ `uniform vec4 uTopCut;   // the height above which a splat is dropped
void modifySplatCenter(inout vec3 center) {}
void modifySplatRotationScale(vec3 originalCenter, vec3 modifiedCenter, inout vec4 rotation, inout vec3 scale) { if (modifiedCenter.y > uTopCut.x) scale = vec3(0.0); }
void modifySplatColor(vec3 center, inout vec4 color) {}`
let topCut = NaN                                                      // the cut height in force (NaN: none)
function setTopCut(y: number) {
  const gs = scan?.gsplat; if (!gs || (isNaN(y) && isNaN(topCut)) || y === topCut) return
  topCut = y; if (isNaN(y)) gs.setWorkBufferModifier(null); else { gs.setParameter('uTopCut', [y, 0, 0, 0]); gs.setWorkBufferModifier({ glsl: TOP_CUT_GLSL }) }
}
function cameraTop() {
  const tv = R!.topView, yaw = tv.yaw + view.topYaw, r = new pc.Quat().setFromEulerAngles(-89.9, yaw, 0)
  const key = `${yaw.toFixed(1)}|${free.w}|${free.h}`
  if (tv.fit?.key !== key) {
    const right = r.transformVector(new pc.Vec3(1, 0, 0), new pc.Vec3()), up = r.transformVector(new pc.Vec3(0, 1, 0), new pc.Vec3())
    const tanV = Math.tan(FOV * Math.PI / 360) * 0.96, tanH = tanV * free.w / free.h
    // every point in the plane's coordinates, with its height over the ground (a point above projects larger, below
    // smaller). The track line and the gates count whole; the paths only by their 2nd to 98th percentile on each axis
    // and in height, so a stray point tens of metres off, a crash or a climb after the finish does not shrink the course
    const pts: [number, number, number][] = [], pa: number[] = [], pb: number[] = [], pd: number[] = []
    const plane = (x: number, y: number, z: number): [number, number, number] => [x * right.x + z * right.z, x * up.x + z * up.z, y - tv.cy]
    for (const q of R!.trackPts) pts.push(plane(q.x, q.y, q.z))
    for (const g of R!.gates) for (const q of g.pts) pts.push(plane(q.x, q.y, q.z))
    // the view is centred on the course itself (the track line and the gates); the paths only widen the fit
    if (!pts.length) pts.push([tv.cx * right.x + tv.cz * right.z, tv.cx * up.x + tv.cz * up.z, 0])
    const r0 = Math.min(...pts.map(p => p[0])), r1 = Math.max(...pts.map(p => p[0])), u0 = Math.min(...pts.map(p => p[1])), u1 = Math.max(...pts.map(p => p[1]))
    const cr = (r0 + r1) / 2, cu = (u0 + u1) / 2
    for (let i = 0; i < tv.all.length; i += 4) { const [a, b, dy] = plane(tv.all[i][0], tv.all[i][1], tv.all[i][2]); pa.push(a); pb.push(b); pd.push(dy) }
    if (pa.length) {
      const pct = (xs: number[], q: number) => { const v = [...xs].sort((a, b) => a - b); return v[Math.min(v.length - 1, Math.floor(v.length * q))] }
      const a0 = pct(pa, 0.02), a1 = pct(pa, 0.98), b0 = pct(pb, 0.02), b1 = pct(pb, 0.98), dy = pct(pd, 0.98)
      pts.push([a0, b0, dy], [a1, b0, dy], [a0, b1, dy], [a1, b1, dy])
    }
    // the height that holds a point at offset o and height dy: o / (h - dy) <= tan, i.e. h >= o / tan + dy
    let h = 20; for (const [a, b, dy] of pts) h = Math.max(h, Math.abs(a - cr) / tanH + dy, Math.abs(b - cu) / tanV + dy)
    tv.fit = { key, x: cr * right.x + cu * up.x, z: cr * right.z + cu * up.z, h }   // right and up are level, so the plane coordinates invert directly
  }
  const camY = tv.cy + tv.fit.h * view.zoom
  cam.setPosition(tv.fit.x, camY, tv.fit.z); cam.setRotation(r)
  cam.camera!.nearClip = Math.max(0.2, camY - (tv.cy + TOP_CUT)); cam.camera!.farClip = camY - tv.cy + TOP_BELOW
  setTopCut(tv.cy + TOP_CUT)
}
function cameraFollow(p: Pilot, tc: number) {
  // behind and above the drone, looking a little ahead along where it is going
  const tt = Math.min(tc, p.end), now = at(p, tt), fwd = at(p, Math.min(p.end, tt + 0.25)).sub(at(p, Math.max(0, tt - 0.35)))
  fwd.y = 0; if (fwd.length() < 0.3) fwd.set(0, 0, -1); fwd.normalize()
  const eye = now.clone().sub(fwd.clone().mulScalar(8 * view.zoom)).add(new pc.Vec3(0, 3 * view.zoom, 0))
  cam.setPosition(eye); cam.lookAt(now.clone().add(fwd.clone().mulScalar(5)))
}
// ?cam=ex,ey,ez,tx,ty,tz,ux,uy,uz[,fov]: a fixed camera (eye, look-at target, up; world metres), to set the scan next
// to a photo taken from a known pose (the fly-through anchors) and compare them
const CAM = (QS.get('cam') ?? '').split(',').map(Number)
function cameraAt(tc: number) {
  if (CAM.length >= 9 && CAM.every(Number.isFinite)) {
    cam.setPosition(CAM[0], CAM[1], CAM[2]); cam.lookAt(new pc.Vec3(CAM[3], CAM[4], CAM[5]), new pc.Vec3(CAM[6], CAM[7], CAM[8]))
    if (CAM.length >= 10) cam.camera!.fov = CAM[9]
    return
  }
  if (camMode === 'free') return cameraFree()
  if (camMode === 'top') return cameraTop()
  if (camMode[0] === 'p') return cameraFollow(R!.P[Number(camMode.slice(1))], tc)
  const { planT, planD, NS, T0 } = R!
  const f = Math.max(0, Math.min(NS - 1, (tc - T0) / DT)), i = Math.floor(f), j = Math.min(NS - 1, i + 1), u = f - i
  const target = new pc.Vec3().lerp(planT[i], planT[j], u), dist = planD[i] + (planD[j] - planD[i]) * u
  const r = new pc.Quat().setFromEulerAngles(view.pitch, yawAt(tc), 0)
  cam.setPosition(r.transformVector(new pc.Vec3(0, 0, dist), new pc.Vec3()).add(target)); cam.setRotation(r)
}

const tmpA = new pc.Vec3(), tmpB = new pc.Vec3()
// a camera switch (and a round switch) glides from where the camera was instead of cutting
const BLEND = 0.7, blendFrom = { pos: new pc.Vec3(), rot: new pc.Quat() }
let blendT = BLEND
function startBlend() { blendFrom.pos.copy(cam.getPosition()); blendFrom.rot.copy(cam.getRotation()); blendT = 0 }
const lastPos = new pc.Vec3(), lastRot = new pc.Quat()
let stillFor = 0, resorted = false
// the track line: a band TRACK_W metres wide on the ground (perspective thins it with distance), dashed along its arc
// length (TRACK_DASH on, TRACK_GAP off); faint white over the scan, darker over the bare grid
const TRACK_W = 0.12, TRACK_DASH = 1.0, TRACK_GAP = 0.7
const trackCol = () => sceneMissing ? hex('#030a18') : new pc.Color(1, 1, 1)
const trackAlpha = (k: number) => (R!.trackS[k] % (TRACK_DASH + TRACK_GAP) < TRACK_DASH ? (sceneMissing ? 0.3 : 0.18) : 0)
const gridCol = new pc.Color(0.72, 0.74, 0.78)
function updateTrail(mesh: pc.Mesh, p: Pilot, tc: number, c: pc.Color, eye: pc.Vec3, pxWorld: number) {
  // the last TRAIL seconds of the path up to its end, fading by age both ways: alpha-to-coverage (the band is opaque
  // with depth, so a blended alpha is not possible; Saqoosha preferred the MSAA stipple to no alpha) and the width,
  // TRAIL_PX at the drone to 1 px at the old end. After a crash it drains away
  const FPS = R!.FPS, n = Math.round(TRAIL * FPS), pts: pc.Vec3[] = [], age: number[] = []
  for (let k = 0; k <= n && tc - k / FPS >= 0; k++) { const s = tc - k / FPS; if (s > p.end) continue; pts.push(at(p, s)); age.push(k / n) }
  ribbon(mesh, pts, c, k => 1 - age[k], eye, pxWorld, TRAIL_PX, false, k => 1 + (TRAIL_PX - 1) * (1 - age[k]))
}
app.on('update', (dt: number) => {
  if (!R) return
  if (playing && !seeking) { t += dt * speed; if (t >= R.T_END) { t = R.T_END; setPlaying(false) } }
  if (measureFree()) plan()                                             // the layout settled or the window changed
  cameraAt(t)
  if (blendT < BLEND) {
    blendT = Math.min(BLEND, blendT + dt); const u = blendT / BLEND, e = u * u * (3 - 2 * u)
    cam.setPosition(new pc.Vec3().lerp(blendFrom.pos, cam.getPosition(), e)); cam.setRotation(new pc.Quat().slerp(blendFrom.rot, cam.getRotation(), e))
  }
  const moved = cam.getPosition().distance(lastPos) > 1e-4 || Math.abs(Math.abs(cam.getRotation().dot(lastRot)) - 1) > 1e-9
  lastPos.copy(cam.getPosition()); lastRot.copy(cam.getRotation())
  stillFor = moved ? 0 : stillFor + dt; if (moved) resorted = false
  if (!resorted && stillFor >= 0.2) resorted = resortWhenIdle(app)
  const eye = cam.getPosition(), pxWorld = 2 * Math.tan(FOV * Math.PI / 360) / free.h   // world size of one pixel at unit distance
  ribbon(R.trackMesh, showTrack ? R.trackPts : [], trackCol(), trackAlpha, eye, pxWorld, 6, true, k => TRACK_W / (pxWorld * eye.distance(R!.trackPts[k])))   // a world width: the pixels that make TRACK_W at that point's distance
  for (const g of R.gates) ribbon(g.mesh, showTrack && sceneMissing ? g.pts : [], g.col, () => 0.85, eye, pxWorld, 3, true)   // gate frames only without a scan
  if (sceneMissing && R.grid.length) app.drawLines(R.grid, gridCol, true)
  const place = new Map(standings(t).map((s, k) => [s.p, { ...s, k: k + 1 }]))
  for (const d of R.drones) {
    const s = place.get(d.p)!, gone = s.out, now = at(d.p, Math.min(t, d.p.end))
    d.e.setPosition(now); d.m.emissive = gone ? grey : d.col; d.m.update()
    updateTrail(d.trail, d.p, t, gone ? grey : d.col, eye, pxWorld)
    const s_ = cam.camera!.worldToScreen(now, tmpA), behind = cam.forward.dot(tmpB.copy(now).sub(eye)) < 0
    // the tag stays on the canvas: near an edge it slides sideways instead of running under the DVR column
    const hw = d.tag.offsetWidth / 2 + 4, tx = Math.max(hw, Math.min(free.W - hw, s_.x))
    d.tag.style.display = behind ? 'none' : ''; d.tag.style.left = tx + 'px'; d.tag.style.top = s_.y + 'px'
    const lab = s.finished ? 'FIN' : gone ? 'OUT' : t < R.race.start ? '' : `L${s.done + 1}`
    const key = `${s.k}|${lab}|${gone}`
    if (d.tag.dataset.k !== key) { d.tag.dataset.k = key
      const b = el('b', '', String(s.k)); b.style.background = gone ? '#8c9199' : d.p.color
      d.tag.replaceChildren(b, d.p.name); if (lab) d.tag.append(el('small', '', lab)) }
  }
  drawBoard(t); drawTimeline(t); syncVideos()
})
// reset: the time back to before the start, paused; the orbit camera with nothing turned or zoomed; 1x
$<HTMLButtonElement>('resetbtn').onclick = e => {
  (e.currentTarget as HTMLButtonElement).blur(); if (!R) return; setPlaying(false); t = R.T0
  Object.assign(view, { yawOffset: 30, topYaw: 0, pitch: PITCH0, zoom: 1 })
  speedSel.value = '1'; speedSel.onchange!(new Event('change'))
  leaveTop(); startBlend(); camMode = 'orbit'; lastMode = 'orbit'; refreshCam(); plan()
}
window.addEventListener('resize', () => { if (R) { sizeTimeline(); layoutStrip() } })

// the round the URL names (?round=44), else the first
const first = roundFromUrl()
await loadRound(first ?? rounds.rounds[0])
if (!first && QS.has('round')) { say(`round ${QS.get('round')} is not in rounds.json: showing round ${rounds.rounds[0].round}`); setTimeout(() => say(sceneNote()), 5000) }
if (!first) { const q = new URLSearchParams(location.search); q.set('round', String(rounds.rounds[0].round)); history.replaceState(null, '', '?' + q.toString()) }
if (import.meta.env.DEV) Object.assign(window, { mgpDebug: { cam, get R() { return R }, rounds, at, pc, freeCam, free, view, get mode() { return camMode }, setMode, pick, get scan() { return scan }, get t() { return t }, set t(v: number) { t = v } } })
app.start()
