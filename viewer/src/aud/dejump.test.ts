import { expect, test } from 'bun:test'
import { Dejump } from './dejump'

const run = (pts: [number, number][]) => { const d = new Dejump<number>(); return pts.flatMap(([t, x], k) => d.feed(t, [x, 0, 0], k)) }

test('a lone wrong answer is dropped', () => {
  expect(run([[0, 0], [0.03, 0.5], [0.4, 90], [0.43, 1.5], [0.46, 2]])).toEqual([0, 1, 3, 4])
})
test('a true new place shows once eight answers agree', () => {
  const moved: [number, number][] = Array.from({ length: 9 }, (_, k) => [0.4 + k * 0.03, 60 + k * 0.5])
  expect(run([[0, 0], ...moved.slice(0, 7)])).toEqual([0])
  expect(run([[0, 0], ...moved])).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
})
test('two wrong answers apart from each other do not confirm', () => {
  expect(run([[0, 0], [0.4, 60], [0.43, -50], [0.46, 0.5]])).toEqual([0, 3])
})
