// The live page for the audience (ghostline.saqoo.sh/<event>/watch.html): the heat flying now at the venue, on the scan.
// The venue's answers come through the relay (worker/src/room.ts) over a WebSocket; everything - the drones, the names,
// a heat starting or ending - is shown DELAY seconds late, so the page can be matched to a broadcast running behind.
//   ?e=<event>        the race day (default: the folder the page is published in)
//   ?relay=ws://host  the relay's origin (default: this page's), for a local wrangler dev
// The scene and the course line (track.json, read again every minute: it can arrive or change during the day) are the
// day's folder's.
import './aud/look.css'
import { LiveDraw, SIGMA, type Answer } from './live'
import { clampDelay, DELAY, loadDelay, saveDelay, ServerClock, Timeline, type HMsg, type Msg } from './aud/delay'
import { applyStatic, L, langToggle, words } from './aud/i18n'
import { clock, heatName, visibleIndex, type HeatIndex } from './aud/listing'
import { advance, decode, drawProgress, makeCourse, newProg, type Course, type Prog } from './aud/progress'
import { createStage, hqButton, SLOTS, type Mode, type PilotState } from './aud/stage'

applyStatic()
document.querySelector('header')!.append(langToggle())

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const QS = new URLSearchParams(location.search)
const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()
const BASE = import.meta.env.BASE_URL
const EVENT = (QS.get('e') ?? BASE.split('/')[1] ?? '').replace(/[^a-z0-9-]/g, '')
const RELAY = QS.get('relay') ?? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`
// lag: how far behind the view the drone is drawn. The Gaussian mean (live.ts gauss, σ SIGMA) wants the answers a quarter
// second past the drawn frame in hand, as the control page's top view has them (its delay, 250 ms). How late answers
// reach a page is not fixed: the relay holds them 0.5 s, then the venue's uplink and the relay's: 1.0-1.8 s, at times 3 s,
// on a phone at JDL R7 2026-10-24 (a fixed 0.8 s left the dot holding at its last answer 70% of the frames, then jumping
// metres). So it follows them: the 90th percentile of the newest answer's age over the last 10 s, plus 0.2 s, reached over
// about 3 s (a sudden change would move the drawn time and restart the trail). DELAY adds to it.
const FPS = 30, TRAIL = 3, HOLD_LOST = 1000, LAG_MIN = 0.8, LAG_MAX = 4
let lag = 1.5, lastFrame = 0; const ages: number[][] = []   // [local ms, the newest answer's age ms] over the last 10 s

const stage = createStage($<HTMLCanvasElement>('c'), BASE)
const clk = new ServerClock(), tl = new Timeline()
let delay = loadDelay(EVENT), connected = false, index: HeatIndex | null = null
const note = (s: string) => { $('note').textContent = s; $('note').hidden = !s }

// --- the scene, the course line, the day's title and flags
let sceneReady = false                                     // the scan's progress in the header until it is in
stage.loadScene(f => { $('load').textContent = `3D ${Number.isFinite(f) ? Math.min(99, Math.floor(f * 100)) + '%' : '…'}` }).then(ix => { sceneReady = true; $('load').textContent = ''; if (ix.title) $('title').textContent = words(ix.title) }).catch(e => { console.error(e); note(L('スキャンを​読めなかった', 'Could not load the scan')) })
// a failed read keeps what was there (only a 404 means there is no line yet)
let line: Course | null = null                               // the line as the progress bars measure it (aud/progress.ts)
const course = () => fetch(BASE + 'track.json', { cache: 'no-cache' }).then(r => r.status === 404 ? (stage.setCourse(null), line = null) : r.ok ? r.json().then(j => { stage.setCourse(j?.track ?? null); line = makeCourse(j) }) : null)
  .catch(e => console.warn('track.json', e))
course(); setInterval(course, 60_000)
const heats = () => fetch(BASE + 'heats.json', { cache: 'no-cache' }).then(r => r.ok ? r.json().then(j => { index = j }) : null).catch(e => console.warn('heats.json', e))
heats(); setInterval(heats, 15_000)

// --- the relay: the room's backlog on connecting, then every batch as it comes
let wait = 2000
function connect() {
  const ws = new WebSocket(`${RELAY}/${EVENT}/api/live`)
  ws.onopen = () => { connected = true; wait = 2000 }
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data) as { now: number; msgs?: Msg[]; backlog?: Msg[]; heat?: HMsg | null }
    clk.see(m.now, Date.now())
    tl.push([...(m.heat ? [m.heat] : []), ...(m.backlog ?? []), ...(m.msgs ?? [])])
  }
  ws.onclose = () => { connected = false; setTimeout(connect, wait); wait = Math.min(30_000, wait * 2) }
}
connect()

// --- DELAY, kept in this browser
function showDelay() { $('dv').textContent = delay.toFixed(1) + ' s'; for (const b of Array.from($("dp").children)) b.classList.toggle('on', Number((b as HTMLElement).dataset.s) === delay) }
const setDelay = (s: number) => { delay = clampDelay(s); saveDelay(EVENT, delay); showDelay() }
$('dminus').onclick = () => setDelay(delay - DELAY.step); $('dplus').onclick = () => setDelay(delay + DELAY.step)
$('dp').replaceChildren(...DELAY.presets.map(([label, s]) => { const b = Object.assign(document.createElement('button'), { textContent: `${label} ${s}s`, onclick: () => setDelay(s) }); b.dataset.s = String(s); return b }))
showDelay()

// --- the camera buttons
const cams: [Mode, string][] = [['orbit', 'ORBIT'], ['top', 'TOP']]
function drawCams() {
  $('cams').replaceChildren(...cams.map(([m, label]) => Object.assign(document.createElement('button'), { textContent: label, className: stage.mode === m ? 'on' : '', onclick: () => stage.setMode(m) })),
    Object.assign(document.createElement('button'), { textContent: 'FOLLOW', className: stage.mode[0] === 'p' ? 'on' : '', onclick: () => follow(1) }),
    hqButton(stage, drawCams, () => { $('load').textContent = '' }))
}
stage.onMode = () => drawCams(); drawCams()
function follow(step: number) {                            // the next pilot in the air
  const up = order.filter(s => states.get(s) === 'flying'); if (!up.length) return
  const k = stage.mode[0] === 'p' ? up.indexOf(stage.mode.slice(1)) : -1; stage.setMode(`p${up[(k + step + up.length) % up.length]}`)
}
window.addEventListener('keydown', e => { if (e.key === '1') stage.setMode('orbit'); if (e.key === '2') stage.setMode('top'); if (e.key === '3') follow(1) })

// --- the pilots of the heat shown: their answers on the heat's own clock (frames from its start), drawn by live.ts.
// Every frame each pilot gets the answers of the last 15 s up to the view again (Timeline.between): one that arrived late
// still lands in its place, and a longer delay (the view going back) needs nothing special. The LiveDraw is kept for its trail.
type Run = { draw: LiveDraw; lastA: number; lastX: number; lastTs: number }
let shown: HMsg | null = null, t0 = 0, order: string[] = [], runs = new Map<string, Run>()
const WINDOW = 15_000                                      // more than a trail (3 s) and its fit need
const states = new Map<string, PilotState>()
let progs = new Map<string, Prog>(), caught = false        // each pilot's place along the line this heat; caught: counted from the heat's start
const answers = new WeakMap<Msg, Answer>()                 // one Answer per message, made once
function begin(h: HMsg | null) {
  if (stage.mode[0] === 'p') stage.setMode('orbit')
  shown = h; t0 = h?.t ?? 0; order = h?.order ?? []; runs = new Map(); progs = new Map(); caught = false; states.clear(); stage.clear()
}
const nameOf = (s: string) => shown?.cells[s] ?? ''
function run(s: string) { let r = runs.get(s); if (!r) { r = { draw: new LiveDraw([], FPS), lastA: -1e15, lastX: -1e15, lastTs: Infinity }; r.draw.taper = true; r.draw.sigma = SIGMA; runs.set(s, r) } return r }
function answer(m: Msg & { k: 'a' }): Answer {
  let a = answers.get(m)
  if (!a) { a = { i: Math.round((m.t - t0) / 1000 * FPS), done: (m.t - t0) / 1000, pos: m.p, quat: m.q, inl: m.n, how: 'track' }; answers.set(m, a) }
  return a
}

// a row of the progress band follows that pilot (again: back to Orbit), as the replay's list of pilots does
let progShown: string[] = []
$('lapc').onclick = e => { const s = progShown[Math.floor((e.offsetY - 3) / 26)]; if (s) stage.setMode(stage.mode === `p${s}` ? 'orbit' : `p${s}`) }

function frame() {
  requestAnimationFrame(frame)
  const nowMs = performance.now(), dt = Math.min(0.1, (nowMs - (lastFrame || nowMs)) / 1000); lastFrame = nowMs
  if (!clk.ready()) { note(connected ? '' : L('接続中…', 'Connecting…')); return }
  const view = clk.now(Date.now()) - delay * 1000
  const h = tl.heatAt(view)
  if (h?.id !== shown?.id) begin(h); else if (h) shown = h          // the same heat, perhaps renamed by now
  for (const r of runs.values()) { r.draw.byDone = []; r.lastA = r.lastX = -1e15 }
  for (const m of shown ? tl.between(Math.max(t0 - 5000, view - WINDOW), view) : []) {
    if (m.k !== 'a' && m.k !== 'x') continue
    const r = run(m.s)
    if (m.k === 'x') { r.lastX = m.t; continue }
    r.lastA = m.t; r.draw.byDone.push(answer(m))
  }
  const newest = Math.max(-1e15, ...[...runs.values()].map(r => r.lastA)), age = view - newest
  if (shown && age < 5000) {                                    // answers still coming (not a landed heat's last)
    ages.push([nowMs, age]); while (ages[0][0] < nowMs - 10000) ages.shift()
    const s = ages.map(a => a[1]).sort((a, b) => a - b), want = Math.min(LAG_MAX, Math.max(LAG_MIN, s[Math.floor(s.length * 0.9)] / 1000 + 0.2))
    lag += (want - lag) * Math.min(1, dt / 3)
  }
  // the header: the day, the heat, LIVE
  $('heat').textContent = shown ? words(heatName({ heat: shown.heat, cls: shown.cls, start: shown.t })) : ''
  $('live').classList.toggle('on', !!shown); $('live').textContent = shown ? 'LIVE' : 'NO HEAT'
  note(!connected ? L('接続中…', 'Connecting…') : shown ? '' : L('いま\u200b飛んでいる\u200bヒートは\u200bありません', 'No heat is flying now'))
  const ts = (view - t0) / 1000
  // joining mid-heat: the laps so far, counted once over the answers the relay sent on connecting (its last 120 s: a
  // 90 s run or a race's 4 laps from the start), so a late viewer does not see everyone back on lap 1
  if (shown && line && !caught) {
    for (const m of tl.between(t0 - 5000, view - lag * 1000)) if (m.k === 'a') { let g = progs.get(m.s); if (!g) progs.set(m.s, g = newProg()); advance(line, g, m.p, (m.t - t0) / 1000) }
    caught = true
  }
  for (const [k, s] of order.entries()) {
    const r = runs.get(s), p = stage.pilot(s, SLOTS[k] ?? 'tl')
    const at = view - lag * 1000, st: PilotState = r && at - r.lastA < HOLD_LOST ? 'flying' : r && at - Math.max(r.lastA, r.lastX) < HOLD_LOST ? 'lost' : 'nopic'
    states.set(s, st)
    const d = r?.draw.dot(ts, 'delay', lag)
    if (!r || !d || st === 'nopic') { p.hide(); continue }
    // only the drawn part is fitted: joining mid-heat, or a delay made longer (time going back), starts the trail afresh
    if (ts < r.lastTs || r.draw.trail.length < d.frame - TRAIL * FPS) { r.draw.trail = new Array(Math.max(0, d.frame - TRAIL * FPS)).fill(null); r.draw.trailT = -1 }
    r.lastTs = ts
    const tr = r.draw.trailAt(ts, d.frame, 3 * SIGMA)   // a point settles once the answers its mean reaches (2.5σ) are in
    if (line && st === 'flying') { let g = progs.get(s); if (!g) progs.set(s, g = newProg()); advance(line, g, d.pos, ts - lag) }   // the drawn dot is lag s old: on the answers' clock, as the passes are
    const n = nameOf(s)
    p.show(d.pos, tr.slice(Math.max(0, d.frame - TRAIL * FPS)), st, { name: n, flag: '', note: st === 'lost' ? 'LOST' : '' })
  }
  // the run's progress under the scene: the pilots who have flown this heat, in the heat's order
  // with the lap timing decoder's passes (tools/dvr/rt_passfwd.py) the laps are its own wherever a transponder is matched
  const pr = order.filter(s => progs.has(s)); progShown = pr
  if (shown) decode(progs, tl.passes.filter(x => x.t >= t0 - 5000).map(x => ({ t: (x.t - t0) / 1000, tx: x.tx, hits: x.hits })), ts - lag)
  if (line) drawProgress($<HTMLCanvasElement>('lapc'), line!, pr.map(s => ({ cell: s, name: nameOf(s), color: css('--' + (SLOTS[order.indexOf(s)] ?? 'tl')) || '#5b4bff', prog: progs.get(s)! })), ts - lag, shown?.cls ?? null)
  // the newest heat this viewer has seen end: the replay link (not before the broadcast shows its end)
  const last = index ? visibleIndex(index, clk.now(Date.now()), delay * 1000).done[0] : null
  $('prev').hidden = !last; if (last) { const a = $<HTMLAnchorElement>('prev'); a.href = `heat.html?h=${encodeURIComponent(last.id)}${QS.has('e') ? '&e=' + EVENT : ''}`; a.textContent = `${L('前のヒート', 'Previous heat')} ${words(heatName(last))}${last.heat ? `（${clock(last.start)}）` : ''}${L('を見返す →', ' →')}` }
}
requestAnimationFrame(frame)
$<HTMLAnchorElement>('home').href = BASE + (QS.has('e') ? `?e=${EVENT}` : '')
Object.assign((window as any).ghostline, { watch: { clk, tl, get lag() { return lag }, get delay() { return delay }, get shown() { return shown }, states, get runs() { return runs } } })
