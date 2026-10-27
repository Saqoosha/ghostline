// The race day's front page (ghostline.saqoo.sh/<event>/, published as index.html): the heat flying now, then every heat
// flown, newest first. Seen through the viewer's delay (the one set on the live page): a heat is LIVE here until its end
// has reached their broadcast, and its replay is listed only from then.
import './aud/look.css'
import { loadDelay } from './aud/delay'
import { applyStatic, L, langToggle, words } from './aud/i18n'
import { clock, heatName, visibleIndex, type HeatEntry, type HeatIndex } from './aud/listing'

applyStatic()
document.querySelector('header')!.append(langToggle())

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const QS = new URLSearchParams(location.search)
const BASE = import.meta.env.BASE_URL
const EVENT = (QS.get('e') ?? BASE.split('/')[1] ?? '').replace(/[^a-z0-9-]/g, '')
const keep = QS.has('e') ? `e=${EVENT}` : ''
let ix: HeatIndex | null = null, skew = 0   // skew: the server's clock minus ours, from its Date header

const who = (h: HeatEntry) => h.pilots.filter(Boolean).join('　') || '—'
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> = {}, ...kids: (Node | string)[]) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e }

function render() {
  const delay = loadDelay(EVENT)
  if (!ix) return
  const title = words(ix.title) || EVENT
  $('title').textContent = title
  document.title = `${title} · ghostline`
  const v = visibleIndex(ix, Date.now() + skew, delay * 1000)
  $('now').hidden = !!ix.closed
  $('now').replaceChildren(v.live
    ? el('a', { className: 'now on', href: 'watch.html' + (keep ? '?' + keep : '') }, el('span', { className: 'live on' }, 'LIVE'), el('b', {}, words(heatName(v.live))), el('span', { className: 'who' }, who(v.live)), el('span', { className: 'go' }, L('見る →', 'Watch →')))
    : el('a', { className: 'now', href: 'watch.html' + (keep ? '?' + keep : '') }, el('span', { className: 'live' }, 'NO HEAT'), el('b', {}, L('いま\u200b飛んでいる\u200bヒートは\u200bありません', 'No heat is flying now')), el('span', { className: 'go' }, L('ライブのページ →', 'Live page →'))))
  const row = (h: HeatEntry) => el('li', {}, el('a', { href: `heat.html?h=${encodeURIComponent(h.id)}${keep ? '&' + keep : ''}` },
    el('span', { className: 't' }, clock(h.start)), el('b', {}, h.heat ? words(heatName(h)) : 'HEAT'), el('span', { className: 'who' }, who(h))))
  const heats = ix.closed ? [...v.done].reverse() : v.done, ss = [...(ix.sessions ?? [])].sort((a, b) => a.from - b.from)
  const of = (h: HeatEntry) => ss.filter(s => s.from <= h.start).pop()?.name ?? ''   // the round it is in: the last one begun by its start
  const parts: Node[] = []; let cur: string | null = null
  for (const h of heats) {
    const s = ss.length ? of(h) : null
    if (s !== cur || !parts.length) { cur = s; parts.push(el('h2', {}, words(s) || 'HEATS'), el('ol', { className: 'list' })) }
    parts[parts.length - 1].appendChild(row(h))
  }
  $('list').replaceChildren(...parts)
  $('empty').hidden = v.done.length > 0
}

function load() {
  fetch(BASE + 'heats.json', { cache: 'no-cache' }).then(r => {
    const d = Date.parse(r.headers.get('date') ?? ''); if (Number.isFinite(d)) skew = d - Date.now()
    if (!r.ok) throw new Error(String(r.status)); return r.json()
  }).then(j => { ix = j; $('fail').hidden = true; render() }).catch(e => { console.warn('heats.json', e); $('fail').hidden = false })   // not "no heats yet": the list could not be read
}
load(); setInterval(load, 10_000); setInterval(render, 1000)   // a heat ending reaches a delayed viewer between loads
