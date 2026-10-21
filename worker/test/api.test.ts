import { expect, test } from 'bun:test'
import { applyToIndex, authorized, dropEnded, heatFile, parseEventPath, validEntry, type HeatIndex, type Msg } from '../src/api'

test('heatFile', () => {
  expect(heatFile('heats/151700-h7/R2.mp4')).toEqual({ id: '151700-h7', file: 'R2.mp4', type: 'video/mp4' })
  expect(heatFile('heats/151700/race.json')).toEqual({ id: '151700', file: 'race.json', type: 'application/json' })
  expect(heatFile('heats/151700')).toBeNull()                      // the live.json PUT, the room's
  expect(heatFile('heats/151700/live.json')).toBeNull()            // the room writes it, with the list entry
  expect(heatFile('heats/151700/../x.mp4')).toBeNull()
  expect(heatFile('heats/bad/R2.mp4')).toBeNull()
  expect(heatFile(`heats/151700/${'a'.repeat(24)}.mp4`)?.file).toBe(`${'a'.repeat(24)}.mp4`)   // rt_control.py's longest cell name
  expect(heatFile(`heats/151700/${'a'.repeat(25)}.mp4`)).toBeNull()
})

test('parseEventPath', () => {
  expect(parseEventPath('/jdl-2026-10-25/api/push')).toEqual({ event: 'jdl-2026-10-25', rest: 'push' })
  expect(parseEventPath('/staging-fdf-2026-10-17/api/live')).toEqual({ event: 'staging-fdf-2026-10-17', rest: 'live' })
  expect(parseEventPath('/jdl-2026-10-25/api/heats/151700-h7')).toEqual({ event: 'jdl-2026-10-25', rest: 'heats/151700-h7' })
  expect(parseEventPath('/Bad_Name/api/push')).toBeNull()
  expect(parseEventPath('/jdl-2026-10-25/race.json')).toBeNull()
})
test('authorized', () => {
  const r = (h?: string) => new Request('https://x/', { headers: h ? { authorization: h } : {} })
  expect(authorized(r('Bearer k1'), 'k1')).toBe(true)
  expect(authorized(r('Bearer k2'), 'k1')).toBe(false)
  expect(authorized(r(), 'k1')).toBe(false)
  expect(authorized(r('Bearer '), '')).toBe(false)          // no secret set: nobody gets in
})
test('applyToIndex: start, rename, end', () => {
  let ix: HeatIndex = { event: 'e', title: 'e', live: null, heats: [] }
  ix = applyToIndex(ix, { k: 'h', t: 1000, id: '151700-h7', heat: '7', cls: 'Expert', cells: { R2: 'A', F1: '' }, order: ['R2', 'F1'] })
  expect(ix.live).toEqual({ id: '151700-h7', start: 1000, end: null, heat: '7', cls: 'Expert', pilots: ['A', ''] })
  ix = applyToIndex(ix, { k: 'h', t: 2000, id: '151700-h7', heat: '7', cls: 'Expert', cells: { R2: 'A', F1: 'B' }, order: ['R2', 'F1'] })
  expect(ix.live!.start).toBe(1000); expect(ix.live!.pilots).toEqual(['A', 'B'])
  ix = applyToIndex(ix, { k: 'e', t: 9000, id: '151700-h7' })
  expect(ix.live).toBeNull(); expect(ix.heats[0]).toMatchObject({ id: '151700-h7', end: 9000 })
  expect(applyToIndex(ix, { k: 'e', t: 9500, id: 'other' })).toEqual(ix)   // the end of an unknown heat is ignored
})
test('applyToIndex: a new heat while one is live keeps the old one in the list (review I4)', () => {
  let ix: HeatIndex = { event: 'e', title: 'e', live: null, heats: [] }
  ix = applyToIndex(ix, { k: 'h', t: 1000, id: '100000', heat: null, cls: null, cells: {}, order: ['R2'] })
  ix = applyToIndex(ix, { k: 'h', t: 9000, id: '100500', heat: null, cls: null, cells: {}, order: ['R2'] })
  expect(ix.live!.id).toBe('100500'); expect(ix.heats.map(h => [h.id, h.end])).toEqual([['100000', 9000]])
})
test('applyToIndex: a start of a heat already ended does not make it live again', () => {
  let ix: HeatIndex = { event: 'e', title: 'e', live: null, heats: [] }
  ix = applyToIndex(ix, { k: 'h', t: 1000, id: '100000', heat: null, cls: null, cells: {}, order: ['R2'] })
  ix = applyToIndex(ix, { k: 'e', t: 5000, id: '100000' })
  expect(applyToIndex(ix, { k: 'h', t: 1000, at: 9000, id: '100000', heat: null, cls: null, cells: {}, order: ['R2'] })).toEqual(ix)
})
test('validEntry', () => {
  const ok = { id: '100000', start: 1, end: 2, heat: null, cls: null, pilots: [''] }
  expect(validEntry(ok, '100000')).toBe(true)
  expect(validEntry({ ...ok, end: null }, '100000')).toBe(false)
  expect(validEntry({ ...ok, pilots: undefined }, '100000')).toBe(false)
  expect(validEntry(ok, '100001')).toBe(false)
})
test('dropEnded removes the starts of heats already ended, and nothing else', () => {
  const msgs: Msg[] = [{ k: 'h', t: 1, at: 9, id: 'A', heat: null, cls: null, cells: {}, order: [] }, { k: 'e', t: 5, id: 'A' }, { k: 'a', s: 'R2', t: 9, p: [0, 0, 0], q: [0, 0, 0, 1], n: 1 }, { k: 'h', t: 2, id: 'B', heat: null, cls: null, cells: {}, order: [] }]
  expect(dropEnded(msgs, new Set(['A'])).map(m => m.k + ('id' in m ? m.id : ''))).toEqual(['eA', 'a', 'hB'])
})
