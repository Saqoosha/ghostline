// A heat to replay, from either of its two versions: the venue's answers right after it (live.json, written by the relay)
// or the night rebuild (race.json, tools/dvr/night_heats.py: positions per frame and each pilot's DVR clip).
import { fit, type Answer } from '../live'
import { SLOTS, type Slot } from './stage'

export type HeatRecord = { id: string; start: number; end: number; heat: string | null; cls: string | null
  cells: Record<string, string>; order: string[]; answers: Record<string, number[][]> }   // a row: [t, x, y, z, qx, qy, qz, qw, inliers]
export type RaceJson = { id: string; start: number; end: number; heat: string | null; cls: string | null; fps: number; version: 'night'; order: string[]
  pilots: { stream: string; name: string; pos: (number[] | null)[]; video?: string }[] }
export type Replay = { id: string; start: number; heat: string | null; cls: string | null; version: 'live' | 'night'; fps: number; dur: number
  pilots: { stream: string; slot: Slot; name: string; pos: (number[] | null)[]; video?: string }[] }

const slotOf = (order: string[], s: string): Slot => SLOTS[order.indexOf(s)] ?? 'tl'
const WS = 0.25, GAP = 0.3                             // the fit's half window (s, as the live page's), the longest gap filled (s)

// the answers smoothed onto a grid of frames from the heat's start, as the live page draws them; a frame is empty where
// the answers around it are more than GAP apart, and outside the answers
export function fromLive(r: HeatRecord, fps = 30): Replay {
  const n = Math.max(1, Math.ceil((r.end - r.start) / 1000 * fps) + 1), W = WS * fps
  const pilots = r.order.filter(s => r.answers[s]?.length).map(s => {
    const ans: Answer[] = r.answers[s].map(a => ({ i: Math.round((a[0] - r.start) / 1000 * fps), done: 0, pos: a.slice(1, 4), quat: a.slice(4, 8), inl: a[8], how: 'track' }))
      .sort((a, b) => a.i - b.i)
    const pos: (number[] | null)[] = new Array(n).fill(null)
    let lo = 0, hi = 0, prev = -1                       // the window [lo, hi) of answers within W of the frame; prev: the last answer at or before it
    for (let k = 0; k < n; k++) {
      while (lo < ans.length && ans[lo].i < k - W) lo++
      while (hi < ans.length && ans[hi].i <= k + W) hi++
      while (prev + 1 < ans.length && ans[prev + 1].i <= k) prev++
      const next = prev + 1 < ans.length ? ans[prev + 1] : null, at = prev >= 0 ? ans[prev] : null
      if (!at || (at.i !== k && (!next || next.i - at.i > GAP * fps))) continue
      pos[k] = fit(ans.slice(lo, hi), k, W, true) ?? at.pos
    }
    return { stream: s, slot: slotOf(r.order, s), name: r.cells[s] || s, pos }
  })
  return { id: r.id, start: r.start, heat: r.heat, cls: r.cls, version: 'live', fps, dur: (r.end - r.start) / 1000, pilots }
}

export function fromNight(j: RaceJson): Replay {
  return { id: j.id, start: j.start, heat: j.heat, cls: j.cls, version: 'night', fps: j.fps, dur: (j.end - j.start) / 1000,
    pilots: j.pilots.map(p => ({ stream: p.stream, slot: slotOf(j.order, p.stream), name: p.name || p.stream, pos: p.pos, video: p.video })) }
}
