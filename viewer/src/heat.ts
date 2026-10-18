// A heat to watch again (ghostline.saqoo.sh/<event>/heat.html?h=<heat id>): the night rebuild when there is one
// (heats/<id>/race.json, with each pilot's DVR clip on the right), else the venue's answers from right after it
// (heats/<id>/live.json). A timeline under it: play, pause, drag, speed. No delay here: the front page lists a heat only
// once its end has reached the viewer's broadcast.
//   ?h=<heat id>  ?e=<event> (default: the folder the page is published in)
import './aud/look.css'
import { country, flagOf } from './aud/flags'
import { clock, heatName } from './aud/listing'
import { drawPilots, redrawPilots } from './aud/panel'
import { resortWhenIdle } from './aud/racekit'
import { fromLive, fromNight, type Replay } from './aud/record'
import { createStage, type Mode, type PilotState } from './aud/stage'

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const QS = new URLSearchParams(location.search)
const BASE = import.meta.env.BASE_URL
const EVENT = (QS.get('e') ?? BASE.split('/')[1] ?? '').replace(/[^a-z0-9-]/g, '')
const ID = (QS.get('h') ?? '').replace(/[^\w-]/g, '')
const TRAIL = 3, SYNC = 0.2                                  // seconds of trail; how far a clip may drift before it is moved
const stage = createStage($<HTMLCanvasElement>('c'), BASE)
let rp: Replay | null = null, t = 0, playing = true, speed = 1, countries: Record<string, string> = {}
const note = (s: string) => { $('note').textContent = s; $('note').hidden = !s }
$<HTMLAnchorElement>('home').href = BASE + (QS.has('e') ? `?e=${EVENT}` : '')

stage.loadScene().then(ix => { if (ix.title) $('title').textContent = ix.title }).catch(e => { console.error(e); note('スキャンを読めなかった') })
fetch(BASE + 'track.json').then(r => r.ok ? r.json() : null).then(j => stage.setCourse(j?.track ?? null)).catch(() => {})
fetch(BASE + 'countries.json').then(r => r.ok ? r.json() : {}).then(j => { countries = j ?? {} }).catch(() => {})

const get = (f: string) => fetch(`${BASE}heats/${ID}/${f}`, { cache: 'no-cache' }).then(r => r.ok ? r.json() : null)
async function load() {
  if (!ID) { note('ヒートが指定されていない'); return }
  const night = await get('race.json').catch(() => null)
  rp = night ? fromNight(night) : await get('live.json').then(j => j && fromLive(j)).catch(() => null)
  if (!rp) { note('このヒートの記録が見つからない'); return }
  note('')
  $('heat').textContent = heatName({ heat: rp.heat, cls: rp.cls, start: rp.start }) + (rp.heat ? ` · ${clock(rp.start)}` : '')   // without a number the name is the time
  $('ver').textContent = rp.version === 'night' ? 'DVR 付き' : '速報'
  $('ver').title = rp.version === 'night' ? '録画から作り直した線と、各機の DVR 映像' : '会場でその場に出した位置（夜に DVR 付きの版に置き換わる）'
  $<HTMLInputElement>('seek').max = String(rp.dur)
  const dir = `${BASE}heats/${ID}/`
  $('dvr').hidden = !rp.pilots.some(p => p.video)
  $('dvr').replaceChildren(...rp.pilots.filter(p => p.video).map(p => {
    const box = document.createElement('figure'); box.style.setProperty('--c', `var(--${p.slot})`)
    const v = Object.assign(document.createElement('video'), { src: dir + p.video, muted: true, playsInline: true, preload: 'auto' }); v.dataset.stream = p.stream
    box.append(v, Object.assign(document.createElement('figcaption'), { textContent: p.name === p.stream ? p.stream : `${p.stream}  ${p.name}` })); return box
  }))
  document.body.classList.toggle('with-dvr', !$('dvr').hidden)
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
const videos = () => Array.from(document.querySelectorAll<HTMLVideoElement>('#dvr video'))
function syncVideos(force = false) {                         // the clips follow the timeline (t = 0 is the clip's start)
  for (const v of videos()) {
    if (force || Math.abs(v.currentTime - t) > SYNC) v.currentTime = Math.min(t, v.duration || t)
    if (playing && v.paused && !v.ended && t < (v.duration || Infinity)) v.play().catch(() => {})   // a clip only runs once it is told to
  }
}

// --- the camera buttons, as the live page's
function drawCams() {
  const btn = (m: Mode, label: string) => Object.assign(document.createElement('button'), { textContent: label, className: stage.mode === m ? 'on' : '', onclick: () => stage.setMode(m) })
  const up = rp?.pilots ?? [], k = stage.mode[0] === 'p' ? up.findIndex(p => `p${p.stream}` === stage.mode) : -1
  $('cams').replaceChildren(btn('orbit', 'ORBIT'), btn('top', 'TOP'),
    Object.assign(document.createElement('button'), { textContent: 'FOLLOW', className: k >= 0 ? 'on' : '', onclick: () => { if (up.length) stage.setMode(`p${up[(k + 1) % up.length].stream}`) } }))
}
// once more after the camera stops: a drag or a wheel while paused, the end of a switch's glide
stage.onMode = () => { drawCams(); redrawPilots(); setTimeout(() => { resorted = false }, 800) }; drawCams()
for (const ev of ['pointerup', 'wheel']) $('c').addEventListener(ev, () => { resorted = false })

let last = performance.now(), resorted = false
function frame(now: number) {
  requestAnimationFrame(frame)
  const dt = Math.min(0.1, (now - last) / 1000); last = now
  if (!rp) return
  if (playing) { t += dt * speed; if (t >= rp.dur) { t = rp.dur; setPlaying(false) } }
  else if (!resorted) resorted = resortWhenIdle(stage.app)   // once after it stops, as race.ts: the splat order of a still camera
  if (document.activeElement !== seek) seek.value = String(t)
  $('clock').textContent = `${fmt(t)} / ${fmt(rp.dur)}`
  if (playing) syncVideos()
  const k = Math.min(Math.floor(t * rp.fps), Math.max(0, (rp.pilots[0]?.pos.length ?? 1) - 1))
  const rows: { stream: string; slot: string; name: string; state: PilotState }[] = []
  for (const p of rp.pilots) {
    const sp = stage.pilot(p.stream, p.slot), at = p.pos[k]
    rows.push({ stream: p.stream, slot: p.slot, name: p.name === p.stream ? '' : p.name, state: at ? 'flying' : 'nopic' })
    if (!at) { sp.hide(); continue }
    const name = p.name === p.stream ? '' : p.name
    sp.show(at, p.pos.slice(Math.max(0, k - TRAIL * rp.fps), k + 1), 'flying', { name, flag: name ? flagOf(country(name, countries)) : '', note: '' })
  }
  drawPilots($('pilots'), stage, rows, countries)
}
requestAnimationFrame(frame)
Object.assign((window as any).ghostline, { heat: { get rp() { return rp }, get t() { return t }, set t(v: number) { t = v }, setPlaying } })
