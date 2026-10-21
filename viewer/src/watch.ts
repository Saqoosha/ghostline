// The live page for the audience (ghostline.saqoo.sh/<event>/watch.html): the heat flying now at the venue, on the scan.
// The venue's answers come through the relay (worker/src/room.ts) over a WebSocket; everything - the drones, the names,
// a heat starting or ending - is shown DELAY seconds late, so the page can be matched to a broadcast running behind.
//   ?e=<event>        the race day (default: the folder the page is published in)
//   ?relay=ws://host  the relay's origin (default: this page's), for a local wrangler dev
// The scene, the course line (track.json, read again every minute: it can arrive or change during the day) and
// countries.json ({name: ISO-2}, optional) are the day's folder's.
import './aud/look.css'
import { LiveDraw, type Answer } from './live'
import { clampDelay, DELAY, loadDelay, saveDelay, ServerClock, Timeline, type HMsg, type Msg } from './aud/delay'
import { country, flagOf } from './aud/flags'
import { clock, heatName, visibleIndex, type HeatIndex } from './aud/listing'
import { drawPilots, redrawPilots } from './aud/panel'
import { createStage, SLOTS, type Mode, type PilotState } from './aud/stage'

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const QS = new URLSearchParams(location.search)
const BASE = import.meta.env.BASE_URL
const EVENT = (QS.get('e') ?? BASE.split('/')[1] ?? '').replace(/[^a-z0-9-]/g, '')
const RELAY = QS.get('relay') ?? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`
// LAG: how far behind the view the drone is drawn. An answer reaches the page ~0.55 s after its frame (the relay holds it
// 0.5 s for the tracker's revisions); 0.7 s puts the dot between two answers that have both arrived, so it glides instead
// of holding at the last answer and jumping. DELAY adds to it.
const FPS = 30, TRAIL = 3, HOLD_LOST = 1000, LAG = 0.7

const stage = createStage($<HTMLCanvasElement>('c'), BASE)
const clk = new ServerClock(), tl = new Timeline()
let delay = loadDelay(EVENT), connected = false, countries: Record<string, string> = {}, index: HeatIndex | null = null
const note = (s: string) => { $('note').textContent = s; $('note').hidden = !s }

// --- the scene, the course line, the day's title and flags
stage.loadScene().then(ix => { if (ix.title) $('title').textContent = ix.title }).catch(e => { console.error(e); note('スキャンを読めなかった') })
// a failed read keeps what was there (only a 404 means there is no line yet)
const course = () => fetch(BASE + 'track.json', { cache: 'no-cache' }).then(r => r.status === 404 ? stage.setCourse(null) : r.ok ? r.json().then(j => stage.setCourse(j?.track ?? null)) : null)
  .catch(e => console.warn('track.json', e))
course(); setInterval(course, 60_000)
fetch(BASE + 'countries.json').then(r => r.ok ? r.json() : {}).then(j => { countries = j ?? {} }).catch(() => {})
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
    Object.assign(document.createElement('button'), { textContent: 'FOLLOW', className: stage.mode[0] === 'p' ? 'on' : '', onclick: () => follow(1) }))
}
stage.onMode = () => { drawCams(); redrawPilots() }; drawCams()
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
const answers = new WeakMap<Msg, Answer>()                 // one Answer per message, made once
function begin(h: HMsg | null) {
  if (stage.mode[0] === 'p') stage.setMode('orbit')
  shown = h; t0 = h?.t ?? 0; order = h?.order ?? []; runs = new Map(); states.clear(); stage.clear(); redrawPilots()
}
const nameOf = (s: string) => shown?.cells[s] ?? ''
function run(s: string) { let r = runs.get(s); if (!r) { r = { draw: new LiveDraw([], FPS), lastA: -1e15, lastX: -1e15, lastTs: Infinity }; r.draw.taper = true; runs.set(s, r) } return r }
function answer(m: Msg & { k: 'a' }): Answer {
  let a = answers.get(m)
  if (!a) { a = { i: Math.round((m.t - t0) / 1000 * FPS), done: (m.t - t0) / 1000, pos: m.p, quat: m.q, inl: m.n, how: 'track' }; answers.set(m, a) }
  return a
}

function drawRows() { drawPilots($('pilots'), stage, order.map(s => ({ stream: s, name: nameOf(s), state: states.get(s) ?? 'nopic' })), countries) }

function frame() {
  requestAnimationFrame(frame)
  if (!clk.ready()) { note(connected ? '' : '接続中…'); return }
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
  // the header: the day, the heat, LIVE
  $('heat').textContent = shown ? heatName({ heat: shown.heat, cls: shown.cls, start: shown.t }) : ''
  $('live').classList.toggle('on', !!shown); $('live').textContent = shown ? 'LIVE' : 'NO HEAT'
  note(!connected ? '接続中…' : shown ? '' : 'いま\u200b飛んでいる\u200bヒートは\u200bありません')
  const ts = (view - t0) / 1000
  for (const [k, s] of order.entries()) {
    const r = runs.get(s), p = stage.pilot(s, SLOTS[k] ?? 'tl')
    const at = view - LAG * 1000, st: PilotState = r && at - r.lastA < HOLD_LOST ? 'flying' : r && at - Math.max(r.lastA, r.lastX) < HOLD_LOST ? 'lost' : 'nopic'
    states.set(s, st)
    const d = r?.draw.dot(ts, 'delay', LAG)
    if (!r || !d || st === 'nopic') { p.hide(); continue }
    // only the drawn part is fitted: joining mid-heat, or a delay made longer (time going back), starts the trail afresh
    if (ts < r.lastTs || r.draw.trail.length < d.frame - TRAIL * FPS) { r.draw.trail = new Array(Math.max(0, d.frame - TRAIL * FPS)).fill(null); r.draw.trailT = -1 }
    r.lastTs = ts
    const tr = r.draw.trailAt(ts, d.frame, 0.5)
    const n = nameOf(s)
    p.show(d.pos, tr.slice(Math.max(0, d.frame - TRAIL * FPS)), st, { name: n, flag: n ? flagOf(country(n, countries)) : '', note: st === 'lost' ? 'LOST' : '' })
  }
  drawRows()
  // the newest heat this viewer has seen end: the replay link (not before the broadcast shows its end)
  const last = index ? visibleIndex(index, clk.now(Date.now()), delay * 1000).done[0] : null
  $('prev').hidden = !last; if (last) { const a = $<HTMLAnchorElement>('prev'); a.href = `heat.html?h=${encodeURIComponent(last.id)}${QS.has('e') ? '&e=' + EVENT : ''}`; a.textContent = `前のヒート ${heatName(last)}${last.heat ? `（${clock(last.start)}）` : ''}を見返す →` }
}
requestAnimationFrame(frame)
$<HTMLAnchorElement>('home').href = BASE + (QS.has('e') ? `?e=${EVENT}` : '')
Object.assign((window as any).ghostline, { watch: { clk, tl, get delay() { return delay }, get shown() { return shown }, states, get runs() { return runs } } })
