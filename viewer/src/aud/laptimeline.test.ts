import { expect, test } from 'bun:test'
import { lapNumbers, lapRows, standings, type Official } from './laptimeline'

const off: Official = { session: 's', cells: {
  R2: { name: 'A', laps: [11, 11.5, 12], ends: [21000, 32500, 44500], holeshot: 10000, laps_driven: 3 },
  F1: { name: 'B', laps: [10.5, 11, 23], ends: [21000, 32000, 55000], holeshot: 10500, laps_driven: 4 },   // its 3rd holds 2 laps (a missed pass)
  R4: { name: 'C', laps: [], ends: [], holeshot: 11000, laps_driven: 0 },
} }
const rows = lapRows(off, 0, () => '#000', ['R2', 'F1', 'R4', 'R5'])

test('a missed pass counts the laps it holds', () => expect(rows[1].num).toEqual([1, 2, 4]))

test('the official count bounds the missed laps', () => {
  expect(lapNumbers([17, 18, 138.5, 17], 4)).toEqual([1, 2, 3, 4])            // RotorHazard: a slow lap is one lap
  expect(lapNumbers([20, 20, 20, 60, 40], 8)).toEqual([1, 2, 3, 6, 8])        // Speedhive: 3 missed, in the long laps
  expect(lapNumbers([20, 20, 61], 4)).toEqual([1, 2, 4])                      // fewer missed than the lap's length says
  expect(lapNumbers([16.691, 16.226, 16.448, 16.165], 6)).toEqual([1, 2, 3, 4])   // a higher count with no long lap splits none (JDL 10/24 h4)
  expect(lapNumbers([20, 41], 3)).toEqual([1, 3])                             // two passes: the long one is the missed lap
  expect(lapNumbers([40, 40, 40, 20, 20], 8)).toEqual([2, 4, 6, 7, 8])        // most laps missed a pass
  expect(lapNumbers([16, 16, 24, 16], 6)).toEqual([1, 2, 3, 4])               // a slow lap (1.5x) is not split
  expect(lapNumbers([0, 0, 5], 3)).toEqual([1, 2, 3])                         // a zero lap: no split, no hang
  expect(lapNumbers([20, 20, 41])).toEqual([1, 2, 4])                            // no count: by length
})

test('standings at the playhead show only the passes made by then', () => {
  expect(standings(rows, 25).map(s => [s.pos, s.laps, s.best])).toEqual([[1, 1, 11], [2, 1, 10.5], [0, 0, null]])
  const end = standings(rows, 60)
  expect(end.map(s => [s.pos, s.laps])).toEqual([[2, 3], [1, 4], [0, 0]])
  expect(end[1].best).toBe(10.5)                                            // the 23 s lap of two is no one's best
  expect(standings(rows, 33).map(s => [s.pos, s.laps])).toEqual([[2, 2], [1, 2], [0, 0]])   // the same laps: the earlier last pass ahead
  expect(end[0].time).toBeCloseTo(34.5)
})
