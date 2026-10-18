import { expect, test } from 'bun:test'
import { loadDelay, ServerClock, Timeline, type Msg } from './delay'

const h = (id: string, t: number, cells: Record<string, string> = {}, at = t): Msg => ({ k: 'h', t, at, id, heat: null, cls: null, cells, order: ['R2', 'F1'] })
const e = (id: string, t: number): Msg => ({ k: 'e', t, id })
const a = (t: number): Msg => ({ k: 'a', s: 'R2', t, p: [0, 0, 0], q: [0, 0, 0, 1], n: 100 })

test('ServerClock takes the fastest recent message', () => {
  const c = new ServerClock(); c.see(1000, 0); c.see(1100, 200)   // the second arrived late
  expect(c.now(300)).toBe(1300)
})
test('Timeline.heatAt across an end and the next start', () => {
  const tl = new Timeline(); tl.push([h('A', 0), e('A', 50_000), h('B', 55_000)])
  expect(tl.heatAt(52_000)).toBeNull()
  expect(tl.heatAt(56_000)!.id).toBe('B')
  expect(tl.heatAt(49_000)!.id).toBe('A')
})
test('Timeline shows new names only from when they were sent', () => {
  const tl = new Timeline(); tl.push([h('A', 0, { R2: '' }, 0), h('A', 0, { R2: 'A' }, 10_000)])
  expect(tl.heatAt(5000)!.cells.R2).toBe('')
  expect(tl.heatAt(11_000)!.cells.R2).toBe('A')
})
test('Timeline.endedBy', () => {
  const tl = new Timeline(); tl.push([h('A', 0), e('A', 50_000)])
  expect([...tl.endedBy(49_999)]).toEqual([]); expect([...tl.endedBy(50_000)]).toEqual(['A'])
})
test('loadDelay survives a storage that throws', () => {
  const g = globalThis as any, was = g.localStorage
  g.localStorage = { getItem() { throw new Error('denied') }, setItem() { throw new Error('denied') } }
  try { expect(loadDelay('x')).toBe(0) } finally { g.localStorage = was }
})
test('loadDelay clamps and rounds to 0.5 s', () => {
  const g = globalThis as any, was = g.localStorage, m = new Map<string, string>()
  g.localStorage = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => m.set(k, v) }
  try { m.set('ghostline-delay:x', '99'); expect(loadDelay('x')).toBe(60); m.set('ghostline-delay:x', '7.3'); expect(loadDelay('x')).toBe(7.5) } finally { g.localStorage = was }
})
test('Timeline.between returns (from, to] and keeps what it returned', () => {
  const tl = new Timeline(); tl.push([a(1000), a(2000), a(3000)])
  expect(tl.between(0, 3000).length).toBe(3)
  expect(tl.between(1000, 2500).map(m => m.t)).toEqual([2000])        // (from, to]
  expect(tl.between(0, 3000).length).toBe(3)                          // still there
})
test('Timeline.between includes one that arrived late', () => {
  const tl = new Timeline(); tl.push([a(1000), a(3000)]); expect(tl.between(0, 2500).length).toBe(1)
  tl.push([a(2400)]); expect(tl.between(0, 2500).map(m => m.t)).toEqual([1000, 2400])
})
test('Timeline keeps 120 s of answers', () => {
  const tl = new Timeline(); tl.push([a(0), a(100_000), a(130_000)])
  expect(tl.between(-1, 200_000).map(m => m.t)).toEqual([100_000, 130_000])
})
test('an ended heat stays ended when its start arrives again afterwards (review C1)', () => {
  const tl = new Timeline(); tl.push([h('A', 0, {}, 0), h('A', 0, {}, 100_000), e('A', 105_000), h('A', 0, {}, 110_000), h('A', 0, {}, 120_000)])
  expect(tl.heatAt(104_000)!.id).toBe('A')
  expect(tl.heatAt(115_000)).toBeNull(); expect(tl.heatAt(500_000)).toBeNull()
})
test('a reconnect pushing the backlog again adds nothing twice', () => {
  const tl = new Timeline(), backlog = [h('A', 0, {}, 0), a(1000), a(2000), e('A', 3000)]
  tl.push(backlog); tl.push(backlog)
  expect(tl.between(0, 5000).length).toBe(2); expect(tl.marks.length).toBe(2)
})
