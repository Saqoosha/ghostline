import { expect, test } from 'bun:test'
import { fromLive, fromNight, type HeatRecord } from './record'

const START = 1_792_211_516_000
const rows = (from: number, to: number, f: (s: number) => number[]) => { const out: number[][] = []; for (let s = from; s <= to + 1e-9; s += 1 / 30) out.push([Math.round(START + s * 1000), ...f(s), 0, 0, 0, 1, 200]); return out }
const rec = (answers: Record<string, number[][]>, cells: Record<string, string> = {}): HeatRecord =>
  ({ id: '133156', start: START, end: START + 4000, heat: null, cls: null, cells, order: ['R2', 'F1', 'R4', 'R5'], answers })

test('fromLive puts the answers on a 30 fps grid', () => {
  const r = fromLive(rec({ R2: rows(0, 3, s => [s, 1, 0]) }))
  expect(r.version).toBe('live'); expect(r.fps).toBe(30); expect(r.dur).toBeCloseTo(4, 1)
  expect(r.pilots[0].pos[45]![0]).toBeCloseTo(1.5, 2)
})
test('fromLive leaves a gap in the answers empty', () => {
  const r = fromLive(rec({ R2: [...rows(0, 1, s => [s, 1, 0]), ...rows(2, 3, s => [s, 1, 0])] }))
  expect(r.pilots[0].pos[45]).toBeNull()
  expect(r.pilots[0].pos[15]).not.toBeNull()
})
test('fromLive names a cell without a name by its own name, and slots by place', () => {
  const r = fromLive(rec({ F1: rows(0, 1, s => [s, 1, 0]), R5: rows(0, 1, s => [0, 1, s]) }, { F1: '' , R5: 'Alex' }))
  expect(r.pilots.map(p => [p.stream, p.slot, p.name])).toEqual([['F1', 'tr', 'F1'], ['R5', 'br', 'Alex']])
})
test('fromNight reads race.json as it is', () => {
  const r = fromNight({ id: 'h', start: START, end: START + 1000, heat: '7', cls: 'Expert', fps: 30, version: 'night', order: ['R2', 'F1'],
    pilots: [{ stream: 'F1', name: 'B', pos: [[0, 1, 2], null], video: 'F1.mp4' }] })
  expect(r.version).toBe('night'); expect(r.pilots[0]).toMatchObject({ stream: 'F1', slot: 'tr', name: 'B', video: 'F1.mp4' }); expect(r.dur).toBe(1)
})
