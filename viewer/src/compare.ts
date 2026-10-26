// Flights side by side (ghostline.saqoo.sh/<event>/compare.html?f=<heat id>~<pilot>&f=...): up to four pilots' flights from
// any of the day's heats, each moved in time so that it passes the start gate at the same moment (its holeshot: the official
// one when the heat has Speedhive's laps, else its first time round past the course line's start, as the progress band
// counts), and replayed together over the scan with the progress band - places by the distance run since the holeshot.
//   ?f=<heat id>~<name>   a flight (repeat, up to 4)   ?e=<event> (default: the folder the page is published in)
import './aud/look.css'
import { applyStatic, L, langToggle, words } from './aud/i18n'
import { clock, heatName, type HeatEntry, type HeatIndex } from './aud/listing'
import { advance, drawProgress, makeCourse, newProg, progressOf, RACE_LAPS, type Course } from './aud/progress'
import { resortWhenIdle } from './aud/racekit'
import { fromLive, fromNight, heldFrom, type Replay } from './aud/record'
import { createStage, hqButton, SLOTS, type Mode } from './aud/stage'

applyStatic()
document.querySelector('header')!.append(langToggle())

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const QS = new URLSearchParams(location.search)
const BASE = import.meta.env.BASE_URL
const EVENT = (QS.get('e') ?? BASE.split('/')[1] ?? '').replace(/[^a-z0-9-]/g, '')
const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()
const MAX = 4, PRE = 3, TRAIL = 3, HOLD = 5              // flights at once; seconds shown before the holeshot; seconds of trail; a lost flight held this long
const note = (s: string) => { $('note').textContent = s; $('note').hidden = !s }
const stage = createStage($<HTMLCanvasElement>('c'), BASE)
$<HTMLAnchorElement>('home').href = BASE + (QS.has('e') ? `?e=${EVENT}` : '')

// a flight: one pilot of one heat, its frames, where it is on the line in each, and its holeshot (s from the heat's start)
type Flight = { key: string; id: string; name: string; label: string; cls: string | null; race: boolean; fps: number
  pos: (number[] | null)[]; arc: (number | null)[]; lap: number[]; hs: number; fin: number | null; video: string | null }   // video: its DVR clip (t = 0 the heat record's start)
let line: Course | null = null, ix: HeatIndex | null = null, flights: Flight[] = [], sceneReady = false
let t = 0, playing = false, speed = 1, dur = 0, resorted = false
// ALL: each flight's whole line, from PRE s before its holeshot to its end (thin and pale), under its sphere and trail; this browser remembers
let all = (() => { try { return localStorage.getItem('ghostline-compare-all') === '1' } catch { return false } })()

const loading = (f: number) => note(`${L('3D を​読み込み中', 'Loading 3D')} ${Number.isFinite(f) ? Math.min(99, Math.floor(f * 100)) + '%' : '…'}`)
loading(0)
stage.loadScene(loading).then(r => { if (r.title) $('title').textContent = words(r.title); sceneReady = true; refresh() }).catch(e => { console.error(e); note(L('スキャンを​読めなかった', 'Could not load the scan')) })
const course = fetch(BASE + 'track.json').then(r => r.ok ? r.json() : null).then(j => { stage.setCourse(j?.track ?? null); line = makeCourse(j) }).catch(() => {})
fetch(BASE + 'heats.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : null).then(j => { ix = j; drawPicker() }).catch(e => console.warn('heats.json', e))

// --- a flight from its heat's record (the night version when there is one), its holeshot and its laps
const records = new Map<string, Promise<Replay | null>>()
const record = (id: string) => {
  let p = records.get(id)
  if (!p) {
    const get = (f: string) => fetch(`${BASE}heats/${id}/${f}`, { cache: 'no-cache' }).then(r => r.ok ? r.json() : null)
    p = get('race.json').catch(() => null).then(n => n ? fromNight(n) : get('live.json').then(j => j && fromLive(j))).catch(() => null)
    records.set(id, p)
  }
  return p
}
async function loadFlight(key: string): Promise<Flight | string> {
  const [id, name] = key.split('~'); const rp = await record(id); await course
  if (!rp || !line) return `${name}${L('：記録が​見つからない', ': record not found')}`
  const p = rp.pilots.find(x => x.name === name); if (!p) return `${name}${L('：この​ヒートに​いない', ': not in this heat')}`
  const g = newProg(), arc: (number | null)[] = [], lap: number[] = []
  p.pos.forEach((q, k) => { if (q) advance(line!, g, q, k / rp.fps); arc.push(g.arc); lap.push(g.lap) })
  const off = rp.official?.cells[p.stream]?.holeshot, hs = off ? (off - rp.start) / 1000 : g.hs
  if (hs === null || hs === undefined) return `${name}${L('：スタートの​ゲートを​通っていない', ': never passed the start gate')}`
  const race = /決勝/.test(rp.cls ?? '')   // raw Japanese cls: do not translate before the test
  return { key, id, name, label: name, cls: rp.cls, race, fps: rp.fps, pos: p.pos, arc, lap, hs, fin: null, video: p.video ? `${BASE}heats/${id}/${p.video}` : null }
}

// --- the flights picked, kept in the URL (so the comparison can be sent as a link)
let keys = QS.getAll('f').slice(0, MAX)
function saveUrl() { const q = new URLSearchParams(QS.has('e') ? { e: EVENT } : {}); for (const k of keys) q.append('f', k); history.replaceState(null, '', '?' + q.toString()) }
async function refresh() {
  const got = await Promise.all(keys.map(loadFlight)), bad = got.filter((x): x is string => typeof x === 'string')
  flights = got.filter((x): x is Flight => typeof x !== 'string')
  for (const f of flights) f.label = flights.filter(o => o.name === f.name).length > 1 ? `${f.name} · ${heatOf(f.id)}` : f.name   // the same pilot twice: which heat
  stage.clear(); dur = Math.max(0, ...flights.map(f => f.pos.length / f.fps - f.hs + PRE))
  $<HTMLInputElement>('seek').max = String(dur); t = Math.min(t, dur); drawPicker(); drawDvr()
  if (!sceneReady) return
  note(bad.length ? bad.join('　') : flights.length ? '' : L('左の​一覧から​比べる​飛行を​選ぶ（4 つまで）', 'Pick up to 4 flights from the list on the left'))
  if (flights.length && !playing && t === 0) setPlaying(true)
}
refresh()
const heatOf = (id: string) => { const h = ix?.heats.find(x => x.id === id); return h ? words(heatName(h)) : id }

// --- the picker: the day's heats by round, a button per pilot
function drawPicker() {
  $('chosen').textContent = keys.length ? L(`${keys.length} / ${MAX} 選んだ`, `${keys.length} / ${MAX} picked`) : L(`比べる​飛行を​選ぶ（${MAX} つまで）`, `Pick up to ${MAX} flights to compare`)
  $('chosen').classList.toggle('full', keys.length >= MAX)
  if (!ix) return
  const ss = [...(ix.sessions ?? [])].sort((a, b) => a.from - b.from), of = (h: HeatEntry) => ss.filter(s => s.from <= h.start).pop()?.name ?? ''
  const heats = [...ix.heats].filter(h => h.end !== null).sort((a, b) => a.start - b.start), out: HTMLElement[] = []; let cur: string | null = null
  for (const h of heats) {
    const s = of(h); if (s !== cur) { cur = s; out.push(Object.assign(document.createElement('h3'), { textContent: words(s) || 'HEATS' })) }
    const box = document.createElement('div'); box.className = 'heat'
    box.append(Object.assign(document.createElement('b'), { textContent: `${clock(h.start)}　${h.heat ? words(heatName(h)) : ''}` }))
    for (const name of h.pilots.filter(Boolean)) {
      const key = `${h.id}~${name}`, k = keys.indexOf(key), b = Object.assign(document.createElement('button'), { textContent: name, className: k >= 0 ? 'on' : '' })
      if (k >= 0) b.style.setProperty('--c', `var(--${SLOTS[k]})`)
      b.onclick = () => { keys = k >= 0 ? keys.filter(x => x !== key) : keys.length < MAX ? [...keys, key] : keys; saveUrl(); t = 0; setPlaying(false); refresh() }
      box.append(b)
    }
    out.push(box)
  }
  $('heats').replaceChildren(...out)
}
$('picktoggle').onclick = () => document.body.classList.toggle('picking')

// --- the DVR clips: one per flight with a clip, each at its own time (t - PRE + its holeshot). While they play, the first one
// running is the clock, as the replay page's (seeking whenever two clocks drift stalls the clips); the others are moved only
// when DRIFT s off; before a clip's start or after its end it waits
const DRIFT = 0.5
let clips: { f: Flight; v: HTMLVideoElement }[] = []
function drawDvr() {
  const boxes: HTMLElement[] = []; clips = []
  flights.forEach((f, i) => {
    if (!f.video) return
    const v = Object.assign(document.createElement('video'), { src: f.video, muted: true, playsInline: true, preload: 'auto' })
    const box = document.createElement('figure'); box.style.setProperty('--c', `var(--${SLOTS[i]})`)
    box.append(v, Object.assign(document.createElement('figcaption'), { textContent: f.label })); boxes.push(box); clips.push({ f, v })
  })
  $('dvr').replaceChildren(...boxes); $('dvr').hidden = !clips.length; document.body.classList.toggle('with-dvr', clips.length > 0)
}
const clipAt = (f: Flight) => t - PRE + f.hs
function syncClips(force = false) {
  for (const { f, v } of clips) {
    const at = clipAt(f), end = v.duration || Infinity
    if (at < 0 || at >= end) { if (!v.paused) v.pause(); continue }
    if (force || Math.abs(v.currentTime - at) > (playing ? DRIFT : 0.2)) v.currentTime = at
    v.playbackRate = speed
    if (playing && v.paused) v.play().catch(() => {}); else if (!playing && !v.paused) v.pause()
  }
}
const clockClip = () => clips.find(c => !c.v.paused && !c.v.ended && c.v.readyState >= 3) ?? null

// --- the timeline, as the replay page's
const seek = $<HTMLInputElement>('seek')
const fmt = (s: number) => `${s < 0 ? '-' : ''}${Math.floor(Math.abs(s) / 60)}:${String(Math.floor(Math.abs(s) % 60)).padStart(2, '0')}`
function setPlaying(v: boolean) { playing = v; resorted = false; $('play').textContent = v ? 'PAUSE' : 'PLAY' }
$('play').onclick = () => { if (t >= dur) t = 0; setPlaying(!playing) }
seek.oninput = () => { t = Number(seek.value); resorted = false; syncClips(true) }
$('speed').replaceChildren(...[0.5, 1, 2].map(v => Object.assign(document.createElement('button'), { textContent: `${v}×`, className: v === speed ? 'on' : '',
  onclick: (e: Event) => { speed = v; for (const b of Array.from($('speed').children)) b.classList.toggle('on', b === e.target) } })))
window.addEventListener('keydown', e => { if (e.key === ' ') { $('play').click(); e.preventDefault() } if (e.key === '1') stage.setMode('orbit'); if (e.key === '2') stage.setMode('top') })
function drawCams() {
  const btn = (m: Mode, label: string) => Object.assign(document.createElement('button'), { textContent: label, className: stage.mode === m ? 'on' : '', onclick: () => stage.setMode(m) })
  const k = stage.mode[0] === 'p' ? flights.findIndex(f => `p${f.key}` === stage.mode) : -1
  $('cams').replaceChildren(btn('orbit', 'ORBIT'), btn('top', 'TOP'),
    Object.assign(document.createElement('button'), { textContent: 'FOLLOW', className: k >= 0 ? 'on' : '', onclick: () => { if (flights.length) stage.setMode(`p${flights[(k + 1) % flights.length].key}`) } }),
    Object.assign(document.createElement('button'), { textContent: 'ALL', title: L('飛行の軌跡を全部', 'Whole flight paths'), className: all ? 'on' : '', onclick: () => { all = !all; try { localStorage.setItem('ghostline-compare-all', all ? '1' : '0') } catch { /* private window */ } drawCams() } }),
    hqButton(stage, drawCams, () => note('')))
}
stage.onMode = () => { drawCams(); setTimeout(() => { resorted = false }, 800) }; drawCams()
for (const ev of ['pointerup', 'wheel']) $('c').addEventListener(ev, () => { resorted = false })
$('lapc').onclick = e => { const f = flights[Math.floor((e.offsetY - 3) / 26)]; if (f) stage.setMode(stage.mode === `p${f.key}` ? 'orbit' : `p${f.key}`) }

// --- each frame: every flight at the same time since its own holeshot (t = PRE is the holeshot)
let last = performance.now()
function frame(now: number) {
  requestAnimationFrame(frame)
  const dt = Math.min(0.1, (now - last) / 1000); last = now
  if (!sceneReady || !flights.length) return
  if (playing) { const c = clockClip(); t = c ? c.v.currentTime + PRE - c.f.hs : t + dt * speed; if (t >= dur) { t = dur; setPlaying(false) } }
  else if (!resorted) resorted = resortWhenIdle(stage.app)
  syncClips()
  if (document.activeElement !== seek) seek.value = String(t)
  $('clock').textContent = `${fmt(t - PRE)} / ${fmt(dur - PRE)}`
  const race = flights.every(f => f.race), ql = line ? Object.values(line.qualLaps) : []
  const laps = race ? RACE_LAPS : Math.max(...flights.map(f => line?.qualLaps[f.cls ?? ''] ?? (ql.length ? Math.max(...ql) : RACE_LAPS)))
  const rows = flights.map((f, i) => {
    const at = (t - PRE + f.hs) * f.fps, k = Math.floor(at), u = at - k, here = f.pos[k] ?? null, next = f.pos[k + 1] ?? null
    const sp = stage.pilot(f.key, SLOTS[i]), whole = stage.pilot(`${f.key}#line`, SLOTS[i]); whole.line = true
    const run = f.pos.slice(Math.max(0, Math.floor((f.hs - PRE) * f.fps))), any = run.find(Boolean)
    if (all && any) whole.show(any, run, 'flying', { name: '', flag: '', note: '' }); else whole.hide()
    if (here) {
      const head = next ? here.map((v, j) => v + (next[j] - v) * u) : here
      sp.show(head, [...f.pos.slice(Math.max(0, k - TRAIL * f.fps), Math.max(0, k + 1)), head], 'flying', { name: f.label, flag: '', note: '' })
    } else {                                                  // a gap: held where last seen, in its colour, up to HOLD s (as the replay page)
      const j = k >= 0 ? heldFrom(f.pos, k, HOLD * f.fps) : -1
      if (j >= 0) sp.show(f.pos[j]!, f.pos.slice(Math.max(0, j - TRAIL * f.fps), j + 1), 'flying', { name: f.label, flag: '', note: '' }); else sp.hide()
    }
    const kk = Math.max(0, Math.min(f.lap.length - 1, k))
    if (f.fin === null && line && f.lap[kk] > 0 && progressOf(line, { arc: f.arc[kk], lap: f.lap[kk], hs: null, done: null }) >= laps) f.fin = t
    return { cell: '', name: f.label, color: css('--' + SLOTS[i]) || '#5b4bff', prog: { arc: k >= 0 ? f.arc[kk] : null, lap: k >= 0 ? f.lap[kk] : 0, hs: t >= PRE ? PRE : null, done: f.fin !== null && f.fin <= t ? f.fin : null } }
  })
  if (line) drawProgress($<HTMLCanvasElement>('lapc'), line, rows, t, flights[0].cls, { laps, places: true })
}
requestAnimationFrame(frame)
Object.assign((window as any).ghostline, { compare: { get flights() { return flights }, get t() { return t }, set t(v: number) { t = v } } })
