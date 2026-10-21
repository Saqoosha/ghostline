// The race day's front page (ghostline.saqoo.sh/<event>/, published as index.html): the heat flying now, then every heat
// flown, newest first. Seen through the viewer's delay (the one set on the live page): a heat is LIVE here until its end
// has reached their broadcast, and its replay is listed only from then.
import './aud/look.css'
import { loadDelay } from './aud/delay'
import { country, flagOf } from './aud/flags'
import { clock, heatName, visibleIndex, type HeatEntry, type HeatIndex } from './aud/listing'

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const QS = new URLSearchParams(location.search)
const BASE = import.meta.env.BASE_URL
const EVENT = (QS.get('e') ?? BASE.split('/')[1] ?? '').replace(/[^a-z0-9-]/g, '')
const keep = QS.has('e') ? `e=${EVENT}` : ''
let ix: HeatIndex | null = null, skew = 0, countries: Record<string, string> = {}   // skew: the server's clock minus ours, from its Date header

const who = (h: HeatEntry) => h.pilots.filter(Boolean).map(n => `${flagOf(country(n, countries))} ${n}`.trim()).join('　') || '—'
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> = {}, ...kids: (Node | string)[]) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e }

function render() {
  const delay = loadDelay(EVENT)
  $('delay').textContent = delay ? `DELAY ${delay.toFixed(1)} s` : ''
  if (!ix) return
  $('title').textContent = ix.title || EVENT
  document.title = `${ix.title || EVENT} · ghostline`
  const v = visibleIndex(ix, Date.now() + skew, delay * 1000)
  $('now').replaceChildren(v.live
    ? el('a', { className: 'now on', href: 'watch.html' + (keep ? '?' + keep : '') }, el('span', { className: 'live on' }, 'LIVE'), el('b', {}, heatName(v.live)), el('span', { className: 'who' }, who(v.live)), el('span', { className: 'go' }, '見る →'))
    : el('a', { className: 'now', href: 'watch.html' + (keep ? '?' + keep : '') }, el('span', { className: 'live' }, 'NO HEAT'), el('b', {}, 'いま\u200b飛んでいる\u200bヒートは\u200bありません'), el('span', { className: 'go' }, 'ライブのページ →')))
  $('list').replaceChildren(...v.done.map(h => el('li', {}, el('a', { href: `heat.html?h=${encodeURIComponent(h.id)}${keep ? '&' + keep : ''}` },
    el('span', { className: 't' }, clock(h.start)), el('b', {}, h.heat ? heatName(h) : 'HEAT'), el('span', { className: 'who' }, who(h))))))
  $('empty').hidden = v.done.length > 0
}

function load() {
  fetch(BASE + 'heats.json', { cache: 'no-cache' }).then(r => {
    const d = Date.parse(r.headers.get('date') ?? ''); if (Number.isFinite(d)) skew = d - Date.now()
    if (!r.ok) throw new Error(String(r.status)); return r.json()
  }).then(j => { ix = j; $('fail').hidden = true; render() }).catch(e => { console.warn('heats.json', e); $('fail').hidden = false })   // not "no heats yet": the list could not be read
}
fetch(BASE + 'countries.json').then(r => r.ok ? r.json() : {}).then(j => { countries = j ?? {}; render() }).catch(() => {})
load(); setInterval(load, 10_000); setInterval(render, 1000)   // a heat ending reaches a delayed viewer between loads
