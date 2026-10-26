// A heat to replay, from either of its two versions: the venue's answers right after it (live.json, written by the relay)
// or the night rebuild (race.json, tools/dvr/night_heats.py: positions per frame and each pilot's DVR clip).
import { gauss, RUN_GAP, SIGMA, type Answer } from '../live'
import { Dejump } from './dejump'
import type { Official } from './laptimeline'
import { SLOTS, type Slot } from './stage'

export type HeatRecord = { id: string; start: number; end: number; heat: string | null; cls: string | null
  cells: Record<string, string>; order: string[]; answers: Record<string, number[][]>; official?: Official }   // a row: [t, x, y, z, qx, qy, qz, qw, inliers]
export type RaceJson = { id: string; start: number; end: number; heat: string | null; cls: string | null; fps: number; version: 'night'; order: string[]
  pilots: { stream: string; name: string; pos: (number[] | null)[]; video?: string }[]; official?: Official }
export type Replay = { id: string; start: number; heat: string | null; cls: string | null; version: 'live' | 'night'; fps: number; dur: number; order: string[]; official?: Official
  pilots: { stream: string; slot: Slot; name: string; pos: (number[] | null)[]; video?: string }[] }

const slotOf = (order: string[], s: string): Slot => SLOTS[order.indexOf(s)] ?? 'tl'

// the answers smoothed onto a grid of frames from the heat's start, as every page draws them (live.ts gauss: the control
// page's top view's mean); a frame is empty where the answers around it are RUN_GAP or more apart, and outside the answers
export function fromLive(r: HeatRecord, fps = 30): Replay {
  const n = Math.max(1, Math.ceil((r.end - r.start) / 1000 * fps) + 1)
  const pilots = r.order.filter(s => r.answers[s]?.length).map(s => {
    const dj = new Dejump<number[]>(), rows = [...r.answers[s]].sort((a, b) => a[0] - b[0]).flatMap(a => dj.feed(a[0] / 1000, a.slice(1, 4), a))
    const ans: Answer[] = rows.map(a => ({ i: Math.round((a[0] - r.start) / 1000 * fps), done: 0, pos: a.slice(1, 4), quat: a.slice(4, 8), inl: a[8], how: 'track' }))
      .sort((a, b) => a.i - b.i)
    const pos: (number[] | null)[] = new Array(n).fill(null)
    let prev = -1                                       // the last answer at or before the frame
    for (let k = 0; k < n; k++) {
      while (prev + 1 < ans.length && ans[prev + 1].i <= k) prev++
      const next = prev + 1 < ans.length ? ans[prev + 1] : null, at = prev >= 0 ? ans[prev] : null
      if (!at || (at.i !== k && (!next || next.i - at.i >= RUN_GAP * fps))) continue
      pos[k] = gauss(ans, k, SIGMA * fps, RUN_GAP * fps) ?? at.pos
    }
    return { stream: s, slot: slotOf(r.order, s), name: r.cells[s] || s, pos }
  })
  return { id: r.id, start: r.start, heat: r.heat, cls: r.cls, version: 'live', fps, dur: (r.end - r.start) / 1000, order: r.order, official: r.official, pilots }
}

export function fromNight(j: RaceJson): Replay {
  return { id: j.id, start: j.start, heat: j.heat, cls: j.cls, version: 'night', fps: j.fps, dur: (j.end - j.start) / 1000, order: j.order, official: j.official,
    pilots: j.pilots.map(p => ({ stream: p.stream, slot: slotOf(j.order, p.stream), name: p.name || p.stream, pos: smoothFrames(p.pos, j.fps), video: p.video })) }
}
// a position per frame (null where none) through the same mean; an empty frame stays empty
export function smoothFrames(pos: (number[] | null)[], fps: number): (number[] | null)[] {
  const ans: Answer[] = []; pos.forEach((p, i) => { if (p) ans.push({ i, done: 0, pos: p, quat: [0, 0, 0, 1], inl: 0, how: '' }) })
  return pos.map((p, i) => p && (gauss(ans, i, SIGMA * fps, RUN_GAP * fps) ?? p))
}

// the frame a lost pilot is held at: the last one with a position within max frames before k (-1: none). The night rebuild
// fills gaps up to 0.3 s only and the day's answers cover 40-70% of a flight, so a dot that vanished at every gap made the
// race hard to follow (JDL R7 10/25); held where it was last seen, in its colour, it stays on screen through the gap
export function heldFrom(pos: (number[] | null)[], k: number, max: number): number {
  for (let j = Math.min(k, pos.length - 1); j >= 0 && k - j <= max; j--) if (pos[j]) return j
  return -1
}
