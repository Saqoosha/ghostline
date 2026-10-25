// A heat to watch again (ghostline.saqoo.sh/<event>/heat.html?h=<heat id>): the night rebuild when there is one
// (heats/<id>/race.json, with each pilot's DVR clip on the right), else the venue's answers from right after it
// (heats/<id>/live.json). A timeline under it: play, pause, drag, speed. No delay here: the front page lists a heat only
// once its end has reached the viewer's broadcast.
//   ?h=<heat id>  ?e=<event> (default: the folder the page is published in)
import './aud/look.css'
import { clock, heatName } from './aud/listing'
import { resortWhenIdle } from './aud/racekit'
import { fromLive, fromNight, type Replay } from './aud/record'
import { drawLaps, lapRows, timeAt, type LapRow } from './aud/laptimeline'
import { advance, drawProgress, makeCourse, newProg, progressOf, RACE_LAPS, type Course } from './aud/progress'
import { createStage, hqButton, SLOTS, type Mode, type PilotState } from './aud/stage'

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const QS = new URLSearchParams(location.search)
const BASE = import.meta.env.BASE_URL
const EVENT = (QS.get('e') ?? BASE.split('/')[1] ?? '').replace(/[^a-z0-9-]/g, '')
const ID = (QS.get('h') ?? '').replace(/[^\w-]/g, '')
const TRAIL = 3, SYNC = 0.2                                  // seconds of trail; how far a clip may drift before it is moved
const stage = createStage($<HTMLCanvasElement>('c'), BASE)
let rp: Replay | null = null, t = 0, playing = false, sceneReady = false, started = false, speed = 1, laps: LapRow[] = []
const note = (s: string) => { $('note').textContent = s; $('note').hidden = !s }
$<HTMLAnchorElement>('home').href = BASE + (QS.has('e') ? `?e=${EVENT}` : '')

// the scan first: its progress over the scene, and the replay starts once both it and the heat's record are in
const loading = (f: number) => note(`3D を​読み込み中 ${Number.isFinite(f) ? Math.min(99, Math.floor(f * 100)) + '%' : '…'}`)
loading(0)
stage.loadScene(loading).then(ix => { if (ix.title) $('title').textContent = ix.title; sceneReady = true; note(rp ? '' : '読み込み中…'); begin() }).catch(e => { console.error(e); note('スキャンを​読めなかった') })
// ?t=<s>: the replay starts there (the LINK button below copies one)
const T0 = Number(QS.get('t')) || 0
// without ?t: 2 s before the first lift-off, GO as on the venue page's timeline (a pilot's last frame on the launch pads before its
// holeshot: the official one, else its first time past the start gate), not the heat's start, mostly drones waiting on the pads; with no lift-off seen,
// 10 s before the first holeshot (3-6 s from the pads to the gate at JDL R7, a novice up to 15 s). The timeline still goes back
function startAt(): number {
  if (!rp) return 0
  if (line) tracks ??= countLaps(rp, line)
  const hsOf = (cell: string) => { const o = rp!.official?.cells[cell]; return o ? (o.holeshot - rp!.start) / 1000 : tracks?.find(x => x.cell === cell)?.hs ?? null }
  const hs: number[] = [], up: number[] = []
  for (const p of rp.pilots) {
    const h = hsOf(p.stream); if (h === null) continue; hs.push(h)
    if (!pads) continue
    for (let k = Math.min(p.pos.length - 1, Math.floor(h * rp.fps)); k >= Math.max(0, Math.floor((h - 20) * rp.fps)); k--) {
      const q = p.pos[k]; if (q && Math.hypot(q[0] - pads.c[0], q[2] - pads.c[2]) < pads.r) { up.push(k / rp.fps); break }
    }
  }
  return up.length ? Math.min(...up) - 2 : hs.length ? Math.min(...hs) - 10 : 0
}
function begin() { if (sceneReady && rp && trackDone && !started) { started = true; t = Math.max(0, Math.min(rp.dur, QS.has('t') ? T0 : startAt())); syncVideos(true); setPlaying(true) } }
let line: Course | null = null, pads: { c: number[]; r: number } | null = null, trackDone = false   // trackDone: the line is in (or there is none): the replay may start
fetch(BASE + 'track.json').then(r => r.ok ? r.json() : null).then(j => { stage.setCourse(j?.track ?? null); line = makeCourse(j); if (j?.launch) pads = { c: j.launch.centre, r: j.launch.radius } }).catch(() => {}).finally(() => { trackDone = true; begin() })

const get = (f: string) => fetch(`${BASE}heats/${ID}/${f}`, { cache: 'no-cache' }).then(r => r.ok ? r.json() : null)
async function load() {
  if (!ID) { note('ヒートが​指定されていない'); return }
  const night = await get('race.json').catch(() => null)
  rp = night ? fromNight(night) : await get('live.json').then(j => j && fromLive(j)).catch(() => null)
  if (!rp) { note('この​ヒートの記録が​見つからない'); return }
  if (sceneReady) note('')
  $('heat').textContent = heatName({ heat: rp.heat, cls: rp.cls, start: rp.start }) + (rp.heat ? ` · ${clock(rp.start)}` : '')   // without a number the name is the time
  $('ver').textContent = rp.version === 'night' ? 'DVR 付き' : '速報'
  $('ver').title = rp.version === 'night' ? '録画から作り直した線と、各機の DVR 映像' : '会場でその場に出した位置（夜に DVR 付きの版に置き換わる）'
  $<HTMLInputElement>('seek').max = String(rp.dur)
  begin()
  const dir = `${BASE}heats/${ID}/`
  $('dvr').hidden = !rp.pilots.some(p => p.video)
  $('dvr').replaceChildren(...rp.pilots.filter(p => p.video).map(p => {
    const box = document.createElement('figure'); box.style.setProperty('--c', `var(--${p.slot})`)
    const v = Object.assign(document.createElement('video'), { src: dir + p.video, muted: true, playsInline: true, preload: 'auto' }); v.dataset.stream = p.stream
    box.append(v, Object.assign(document.createElement('figcaption'), { textContent: p.name })); return box
  }))
  document.body.classList.toggle('with-dvr', !$('dvr').hidden)
  // the official laps (race.json / live.json "official"), under the scene: each cell in its place's colour (look.css --tl ...)
  const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()
  laps = lapRows(rp.official, rp.start, cell => css('--' + (SLOTS[rp!.order.indexOf(cell)] ?? 'tl')) || '#5b4bff', rp.order)
}
const lapC = $<HTMLCanvasElement>('lapc')
lapC.onclick = e => {                                       // the official laps: a click seeks there; the progress: a row follows that pilot
  if (laps.length) { const v = timeAt(lapC, laps, e.offsetX); if (v !== null && rp) { t = Math.max(0, Math.min(rp.dur, v)); syncVideos(true); resorted = false } return }
  const s = progShown[Math.floor((e.offsetY - 3) / 26)]; if (s) stage.setMode(stage.mode === `p${s}` ? 'orbit' : `p${s}`)
}

// a heat without the official laps: the run's progress instead (aud/progress.ts, as the live page's), counted once over
// the whole record: each frame's place on the line and laps, each pilot's holeshot and, past 4 laps, its finish
type Track = { cell: string; name: string; color: string; arc: (number | null)[]; lap: number[]; hs: number | null; fin: number | null }
let tracks: Track[] | null = null, progShown: string[] = []
function countLaps(rp: Replay, c: Course): Track[] {
  const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()
  return rp.pilots.map(p => {
    const g = newProg(), arc: (number | null)[] = [], lap: number[] = []; let fin: number | null = null
    p.pos.forEach((q, k) => { if (q) advance(c, g, q, k / rp.fps); arc.push(g.arc); lap.push(g.lap); if (fin === null && progressOf(c, g) >= RACE_LAPS) fin = k / rp.fps })
    return { cell: p.stream, name: p.name === p.stream ? '' : p.name, color: css('--' + p.slot) || '#5b4bff', arc, lap, hs: g.hs, fin }
  })
}
load()

// --- the timeline
const seek = $<HTMLInputElement>('seek')
const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
function setPlaying(v: boolean) { playing = v; resorted = false; $('play').textContent = v ? 'PAUSE' : 'PLAY'; for (const el of videos()) v ? el.play().catch(() => {}) : el.pause() }
$('play').onclick = () => { if (rp && t >= rp.dur) t = 0; setPlaying(!playing) }
seek.oninput = () => { t = Number(seek.value); syncVideos(true); resorted = false }
$('speed').replaceChildren(...[0.5, 1, 2].map(v => Object.assign(document.createElement('button'), { textContent: `${v}×`, className: v === speed ? 'on' : '',
  onclick: (e: Event) => { speed = v; for (const b of Array.from($('speed').children)) b.classList.toggle('on', b === e.target); for (const el of videos()) el.playbackRate = v } })))
window.addEventListener('keydown', e => {
  if (e.key === ' ') { $('play').click(); e.preventDefault() }
  if (e.key === 'ArrowLeft') { t = Math.max(0, t - 5); syncVideos(true) }
  if (e.key === 'ArrowRight' && rp) { t = Math.min(rp.dur, t + 5); syncVideos(true) }
  if (e.key === '1') stage.setMode('orbit'); if (e.key === '2') stage.setMode('top')
})
$('link').onclick = () => {                                 // this heat at this moment, as a link to send
  const q = new URLSearchParams(location.search); q.set('t', t.toFixed(1)); const url = `${location.origin}${location.pathname}?${q}`
  history.replaceState(null, '', `?${q}`)
  const done = (s: string) => { $('link').textContent = s; setTimeout(() => { $('link').textContent = 'LINK' }, 1500) }
  (navigator.clipboard?.writeText(url) ?? Promise.reject()).then(() => done('COPIED'), () => done('URL に入れた'))
}
const videos = () => Array.from(document.querySelectorAll<HTMLVideoElement>('#dvr video'))
// the clips follow the timeline (t = 0 is the clip's start). While they play, the first one running is the clock (frame()
// takes t from it): a seek stalls a clip, and seeking whenever the page's own clock and the clips drifted 0.2 s apart did so
// twice in 5 s on each, 22-26 of 30 frames shown (JDL R7 2026-10-25). The others are moved only when DRIFT s off it
const DRIFT = 0.5
const clockClip = () => videos().find(v => !v.paused && !v.ended && v.readyState >= 3 && t < (v.duration || 0) - 0.1) ?? null
function syncVideos(force = false) {
  for (const v of videos()) {
    if (force || Math.abs(v.currentTime - t) > (playing ? DRIFT : SYNC)) v.currentTime = Math.min(t, v.duration || t)
    if (playing && v.paused && !v.ended && t < (v.duration || Infinity)) v.play().catch(() => {})   // a clip only runs once it is told to
  }
}

// --- the camera buttons, as the live page's
function drawCams() {
  const btn = (m: Mode, label: string) => Object.assign(document.createElement('button'), { textContent: label, className: stage.mode === m ? 'on' : '', onclick: () => stage.setMode(m) })
  const up = rp?.pilots ?? [], k = stage.mode[0] === 'p' ? up.findIndex(p => `p${p.stream}` === stage.mode) : -1
  $('cams').replaceChildren(btn('orbit', 'ORBIT'), btn('top', 'TOP'),
    Object.assign(document.createElement('button'), { textContent: 'FOLLOW', className: k >= 0 ? 'on' : '', onclick: () => { if (up.length) stage.setMode(`p${up[(k + 1) % up.length].stream}`) } }),
    hqButton(stage, drawCams, () => note('')))
}
// once more after the camera stops: a drag or a wheel while paused, the end of a switch's glide
stage.onMode = () => { drawCams(); setTimeout(() => { resorted = false }, 800) }; drawCams()
for (const ev of ['pointerup', 'wheel']) $('c').addEventListener(ev, () => { resorted = false })

let last = performance.now(), resorted = false
function frame(now: number) {
  requestAnimationFrame(frame)
  const dt = Math.min(0.1, (now - last) / 1000); last = now
  if (!rp) return
  if (playing) { const c = clockClip(); t = c ? c.currentTime : t + dt * speed; if (t >= rp.dur) { t = rp.dur; setPlaying(false) } }
  else if (!resorted) resorted = resortWhenIdle(stage.app)   // once after it stops, as race.ts: the splat order of a still camera
  if (document.activeElement !== seek) seek.value = String(t)
  $('clock').textContent = `${fmt(t)} / ${fmt(rp.dur)}`
  if (laps.length) drawLaps(lapC, laps, t)
  else if (line) {
    tracks ??= countLaps(rp, line)
    const f = Math.min(Math.floor(t * rp.fps), (tracks[0]?.lap.length ?? 1) - 1)
    const rows = tracks.filter(x => x.hs !== null && x.hs <= t + 30).map(x => ({ cell: x.cell, name: x.name, color: x.color, prog: { arc: x.arc[f], lap: x.lap[f], hs: x.hs !== null && x.hs <= t ? x.hs : null, done: x.fin !== null && x.fin <= t ? x.fin : null } }))
    progShown = rows.map(x => x.cell); drawProgress(lapC, line, rows, t, rp.cls)
  }
  if (playing) syncVideos()
  const k = Math.min(Math.floor(t * rp.fps), Math.max(0, (rp.pilots[0]?.pos.length ?? 1) - 1)), u = t * rp.fps - k   // u: how far into frame k
  // the dot between frame k and the next by the time, not held at k: held, it moved in 30 Hz steps on a 60-120 Hz screen
  const between = (a: number[], b: number[] | null) => b ? a.map((v, i) => v + (b[i] - v) * u) : a
  for (const p of rp.pilots) {
    const sp = stage.pilot(p.stream, p.slot), at = p.pos[k]
    if (!at) { sp.hide(); continue }
    const name = p.name === p.stream ? '' : p.name
    const head = between(at, p.pos[k + 1] ?? null)
    sp.show(head, [...p.pos.slice(Math.max(0, k - TRAIL * rp.fps), k + 1), head], 'flying', { name, flag: '', note: '' })
  }
}
requestAnimationFrame(frame)
Object.assign((window as any).ghostline, { heat: { get rp() { return rp }, get t() { return t }, set t(v: number) { t = v }, setPlaying } })
