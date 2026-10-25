import { expect, test } from 'bun:test'
import { advance, decode, isRace, makeCourse, newProg, progressOf } from './progress'

// a round course of radius 20 m, 360 points from the start gate; the pads just before its end
const ring = Array.from({ length: 360 }, (_, k) => [20 * Math.cos(k * Math.PI / 180), 0, 20 * Math.sin(k * Math.PI / 180)])
const at = (deg: number) => [20 * Math.cos(deg * Math.PI / 180), 0, 20 * Math.sin(deg * Math.PI / 180)]
const course = makeCourse({ track: ring, gates: [{ centre: at(90) }, { centre: at(180) }], qual_laps: { Pro: 7 } })!

test('the gates sit along a lap', () => {
  expect(course.gates[0]).toBeCloseTo(0.25, 2); expect(course.gates[1]).toBeCloseTo(0.5, 2)
})

test('laps count from the holeshot, and the progress is laps plus the part under way', () => {
  const p = newProg()
  advance(course, p, at(350), 0)                                   // on the pads
  expect(progressOf(course, p)).toBe(0)
  for (let d = 352, t = 1; d <= 360 + 2 * 360 + 90; d += 4, t += 0.1) advance(course, p, at(d % 360), t)
  expect(p.hs).toBeCloseTo(1.2, 5)                                 // through the start at 360°
  expect(p.lap).toBe(3); expect(progressOf(course, p)).toBeCloseTo(2.25, 1)
})

test('a mass start is holeshots within 2 s', () => {
  const p = (hs: number | null) => ({ ...newProg(), hs })
  expect(isRace([p(10), p(11.5), p(null)])).toBe(true)
  expect(isRace([p(10), p(16)])).toBe(false)
  expect(isRace([p(10)])).toBe(false)
})

test('decode: the decoder counts a matched pilot\'s laps from its first pass, a missed pass filled by the line', () => {
  const p = newProg(); p.lap = 3; p.arc = 10; p.hs = 162; p.wraps = [162.4, 178.3, 179.6]
  const q = newProg(); q.lap = 1; q.arc = 10; q.hs = 136.8; q.wraps = [136.8]
  const progs = new Map([['R5', p], ['R2', q]])
  const passes = [{ t: 138.6, tx: 1 }, { t: 157, tx: 1 }, { t: 179.3, tx: 1 }, { t: 196.9, tx: 1 }, { t: 136.9, tx: 2, hits: 120 }, { t: 150, tx: 2, hits: 3 }]
  const of = decode(progs, passes, 200)
  expect(of.get('R5')).toBe(1); expect(of.get('R2')).toBe(2)
  expect(p.dec).toEqual({ n: 4, last: 196.9 })          // 178.3 is within 0.6 of a lap of 179.3: no fill
  expect(q.dec).toEqual({ n: 1, last: 136.9 })            // the 3-hit read is left out
})
