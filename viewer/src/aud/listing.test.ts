import { expect, test } from 'bun:test'
import { visibleIndex, type HeatEntry, type HeatIndex } from './listing'

const NOW = 1_000_000_000
const heat = (id: string, start: number, end: number | null, extra: Partial<HeatEntry> = {}): HeatEntry => ({ id, start, end, heat: '7', cls: 'Expert', pilots: ['A', 'B'], ...extra })
const ix = (live: HeatEntry | null, heats: HeatEntry[]): HeatIndex => ({ event: 'e', title: 'e', live, heats })

test('a heat that ended 40 s ago is still live for a 60 s delay', () => {
  const h = heat('h1', NOW - 200_000, NOW - 40_000), v = visibleIndex(ix(null, [h]), NOW, 60_000)
  expect(v.live?.id).toBe('h1'); expect(v.done).toEqual([])
})
test('the same heat with no delay is done', () => {
  const h = heat('h1', NOW - 200_000, NOW - 40_000), v = visibleIndex(ix(null, [h]), NOW, 0)
  expect(v.live).toBeNull(); expect(v.done.map(x => x.id)).toEqual(['h1'])
})
test('a heat started 10 s ago is not shown to a 20 s delay; the one before is live', () => {
  const prev = heat('h1', NOW - 200_000, NOW - 15_000), next = heat('h2', NOW - 10_000, null)
  const v = visibleIndex(ix(next, [prev]), NOW, 20_000)
  expect(v.live?.id).toBe('h1'); expect(v.done).toEqual([])
})
test('the live heat once its start is past the delay', () => {
  const next = heat('h2', NOW - 30_000, null), v = visibleIndex(ix(next, []), NOW, 20_000)
  expect(v.live?.id).toBe('h2')
})
test('a heat without a number or names still lists', () => {
  const h = heat('133156', NOW - 100_000, NOW - 50_000, { heat: null, cls: null, pilots: ['', ''] })
  expect(visibleIndex(ix(null, [h]), NOW, 0).done[0].id).toBe('133156')
})
