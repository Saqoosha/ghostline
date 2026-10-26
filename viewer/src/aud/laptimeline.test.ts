import { expect, test } from 'bun:test'
import { lapRows, standings, type Official } from './laptimeline'

const off: Official = { session: 's', cells: {
  R2: { name: 'A', laps: [11, 11.5, 12], ends: [21000, 32500, 44500], holeshot: 10000, laps_driven: 3 },
  F1: { name: 'B', laps: [10.5, 11, 23], ends: [21000, 32000, 55000], holeshot: 10500, laps_driven: 4 },   // its 3rd holds 2 laps (a missed pass)
  R4: { name: 'C', laps: [], ends: [], holeshot: 11000, laps_driven: 0 },
} }
const rows = lapRows(off, 0, () => '#000', ['R2', 'F1', 'R4', 'R5'])

test('a missed pass counts the laps it holds', () => expect(rows[1].num).toEqual([1, 2, 4]))

test('standings at the playhead show only the passes made by then', () => {
  expect(standings(rows, 25).map(s => [s.pos, s.laps, s.best])).toEqual([[1, 1, 11], [2, 1, 10.5], [0, 0, null]])
  const end = standings(rows, 60)
  expect(end.map(s => [s.pos, s.laps])).toEqual([[2, 3], [1, 4], [0, 0]])
  expect(end[1].best).toBe(10.5)                                            // the 23 s lap of two is no one's best
  expect(standings(rows, 33).map(s => [s.pos, s.laps])).toEqual([[2, 2], [1, 2], [0, 0]])   // the same laps: the earlier last pass ahead
  expect(end[0].time).toBeCloseTo(34.5)
})
