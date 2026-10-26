// The audience pages' clock and their delay. Everything a viewer sees - answers, names, a heat starting or ending - is
// shown at (the relay's time - their delay), so a page matched to a broadcast running 20 s late never shows what the
// broadcast has not yet. The shapes are the relay's (worker/src/api.ts; tools/dvr/rt_relay.py writes them).
import { Dejump } from './dejump'

export type AMsg = { k: 'a'; s: string; t: number; p: number[]; q: number[]; n: number }
export type XMsg = { k: 'x'; s: string; t: number }
export type HMsg = { k: 'h'; t: number; at?: number; id: string; heat: string | null; cls: string | null; cells: Record<string, string>; order: string[] }
export type EMsg = { k: 'e'; t: number; id: string }
export type PMsg = { k: 'p'; t: number; n: number; tx: number; hits?: number }   // the lap timing decoder's pass (tools/dvr/rt_relay.py, PASSINGS)
export type Msg = AMsg | XMsg | HMsg | EMsg | PMsg

export const DELAY = { min: 0, max: 60, step: 0.5, presets: [['低遅延', 7], ['通常', 20]] as [string, number][] }
const key = (event: string) => 'ghostline-delay:' + event
export const clampDelay = (s: number) => Math.min(DELAY.max, Math.max(DELAY.min, Math.round(s / DELAY.step) * DELAY.step))
export function loadDelay(event: string): number {
  try { const v = Number(localStorage.getItem(key(event))); return Number.isFinite(v) ? clampDelay(v) : 0 } catch { return 0 }
}
export function saveDelay(event: string, s: number) { try { localStorage.setItem(key(event), String(clampDelay(s))) } catch { /* private window */ } }

// the relay's clock on this machine: the largest (server - local) seen in the last 30 s, i.e. the message that came fastest
export class ServerClock {
  private offs: [number, number][] = []
  see(serverNow: number, localNow: number) {
    this.offs.push([localNow, serverNow - localNow])
    while (this.offs.length > 1 && this.offs[0][0] < localNow - 30_000) this.offs.shift()
  }
  ready() { return this.offs.length > 0 }
  now(localNow: number) { return localNow + Math.max(...this.offs.map(o => o[1])) }
}

const when = (m: Msg) => m.k === 'h' ? m.at ?? m.t : m.t
const KEEP = 170_000                                   // the relay's backlog (150 s: a run's laps counted from its start) and margin
// one key per message: a reconnect sends the room's backlog again
const msgKey = (m: Msg) => m.k === 'h' ? `h${m.id}|${m.at ?? m.t}` : m.k === 'e' ? `e${m.id}|${m.t}` : m.k === 'p' ? `p${m.n}` : `${m.k}${m.s}|${m.t}`

export class Timeline {
  private kept: Msg[] = []                             // answers and lost frames, in time order
  marks: Msg[] = []                                    // every h and e, in time order
  passes: PMsg[] = []                                  // the decoder's, in time order
  private newest = 0; private pruned = 0; private seen = new Map<string, number>()
  private dj = new Map<string, Dejump<AMsg>>()        // per pilot: answers that jump wait for the next ones to agree
  push(msgs: Msg[]) {
    for (const m of msgs) {
      const k = msgKey(m); if (this.seen.has(k)) continue; this.seen.set(k, when(m))
      this.newest = Math.max(this.newest, when(m))
      if (m.k === 'h' || m.k === 'e') this.marks.push(m)
      if (m.k === 'x') this.kept.push(m)
      if (m.k === 'p') this.passes.push(m)
      if (m.k === 'a') { let d = this.dj.get(m.s); if (!d) this.dj.set(m.s, d = new Dejump()); this.kept.push(...d.feed(m.t / 1000, m.p, m)) }
    }
    this.kept.sort((a, b) => when(a) - when(b)); this.marks.sort((a, b) => when(a) - when(b))
    const old = this.newest - KEEP; let k = 0; while (k < this.kept.length && when(this.kept[k]) < old) k++
    if (k) this.kept.splice(0, k)
    this.passes.sort((a, b) => a.t - b.t); while (this.passes.length && this.passes[0].t < old) this.passes.shift()
    if (this.newest - this.pruned > 10_000) { this.pruned = this.newest; for (const [s, t] of this.seen) if (t < old) this.seen.delete(s) }   // every 10 s, not every batch
  }
  // the answers and lost frames in (from, to]
  between(from: number, to: number): Msg[] {
    let lo = 0, hi = this.kept.length
    while (lo < hi) { const m = (lo + hi) >> 1; if (when(this.kept[m]) <= from) lo = m + 1; else hi = m }
    const out: Msg[] = []; for (let k = lo; k < this.kept.length && when(this.kept[k]) <= to; k++) out.push(this.kept[k])
    return out
  }
  heatAt(t: number): HMsg | null {                    // the heat on at t, with the names it had then
    let cur: HMsg | null = null; const ended = new Set<string>()
    for (const m of this.marks) {
      if (when(m) > t) break
      if (m.k === 'e') { ended.add(m.id); if (cur?.id === m.id) cur = null }
      else if (m.k === 'h' && !ended.has(m.id) && (!cur || cur.id === m.id || m.t >= cur.t)) cur = m   // a start arriving after the end does not revive it
    }
    return cur
  }
  endedBy(t: number): Set<string> { return new Set(this.marks.filter((m): m is EMsg => m.k === 'e' && m.t <= t).map(m => m.id)) }
}
