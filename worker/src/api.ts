// The live relay's shapes and the pure pieces of its routes (index.ts routes, room.ts holds the state).
// The venue's sender (tools/dvr/rt_relay.py) posts Msgs; the room hands them to every viewer and keeps the day's heat list.
// t is the venue box's wall clock in ms.
export type AMsg = { k: 'a'; s: string; t: number; p: number[]; q: number[]; n: number }   // an answer (a frame's position)
export type XMsg = { k: 'x'; s: string; t: number }                                      // a frame with a picture and no answer
export type HMsg = { k: 'h'; t: number; at?: number; id: string; heat: string | null; cls: string | null; cells: Record<string, string>; order: string[] }
export type EMsg = { k: 'e'; t: number; id: string }
export type Msg = AMsg | XMsg | HMsg | EMsg
export type HeatEntry = { id: string; start: number; end: number | null; heat: string | null; cls: string | null; pilots: string[] }
export type HeatIndex = { event: string; title: string; live: HeatEntry | null; heats: HeatEntry[] }   // heats: newest first

export const EVENT = /^(staging-)?[a-z0-9][a-z0-9-]*$/
export const HEAT_ID = /^\d{6}(-h[\w-]{1,16})?$/

export function parseEventPath(path: string): { event: string; rest: string } | null {
  const m = /^\/([^/]+)\/api\/(.+)$/.exec(path)
  return m && EVENT.test(m[1]) ? { event: m[1], rest: m[2] } : null
}

// a heat's DVR replay, put by the venue right after the heat (rt_relay.py): its clips (<cell>.mp4) and then race.json
export function heatFile(rest: string): { id: string; file: string; type: string } | null {
  const m = /^heats\/([^/]+)\/(race\.json|[\w-]{1,16}\.mp4)$/.exec(rest)
  return m && HEAT_ID.test(m[1]) ? { id: m[1], file: m[2], type: m[2].endsWith('.mp4') ? 'video/mp4' : 'application/json' } : null
}

export function authorized(req: Request, key: string): boolean {
  if (!key) return false
  const got = req.headers.get('authorization') ?? '', want = 'Bearer ' + key
  if (got.length !== want.length) return false
  let d = 0; for (let i = 0; i < want.length; i++) d |= got.charCodeAt(i) ^ want.charCodeAt(i)   // the same time for every wrong key of its length
  return d === 0
}

// h starts the live heat or renames it (same id), e moves it to the top of the list with its end
export function applyToIndex(ix: HeatIndex, m: Msg): HeatIndex {
  if (m.k === 'h' && ix.heats.some(h => h.id === m.id)) return ix          // a start arriving after its heat ended
  if (m.k === 'h') {
    const pilots = m.order.map(s => m.cells[s] ?? '')
    const start = ix.live?.id === m.id ? ix.live.start : m.t
    // another heat still live: its end never came (the sender restarted mid-heat); it ends where this one starts
    const heats = ix.live && ix.live.id !== m.id ? [{ ...ix.live, end: m.t }, ...ix.heats.filter(h => h.id !== ix.live!.id)] : ix.heats
    return { ...ix, heats, live: { id: m.id, start, end: null, heat: m.heat, cls: m.cls, pilots } }
  }
  if (m.k === 'e' && ix.live?.id === m.id) return { ...ix, live: null, heats: [{ ...ix.live, end: m.t }, ...ix.heats.filter(h => h.id !== m.id)] }
  return ix
}

// a heat's list entry as the sender's upload carries it (x-heat-entry): ended, for the heat named in the path
export function validEntry(e: any, id: string): e is HeatEntry & { end: number } {
  return e?.id === id && typeof e.start === 'number' && typeof e.end === 'number' && Array.isArray(e.pilots)
}

// the starts of heats already ended: a late repeat must not reach viewers or become the room's live heat
export function dropEnded(msgs: Msg[], ended: Set<string>): Msg[] { return msgs.filter(m => !(m.k === 'h' && ended.has(m.id))) }
