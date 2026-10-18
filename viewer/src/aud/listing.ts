// The day's heat list (heats.json, written by the relay) as one viewer may see it: shifted back by their delay, so a heat
// that ended on the venue but not yet on the broadcast they watch is still LIVE, and a heat the broadcast has not started
// is not there at all.
export type HeatEntry = { id: string; start: number; end: number | null; heat: string | null; cls: string | null; pilots: string[] }
export type HeatIndex = { event: string; title: string; live: HeatEntry | null; heats: HeatEntry[] }

export function visibleIndex(ix: HeatIndex, serverNow: number, delayMs: number): { live: HeatEntry | null; done: HeatEntry[] } {
  const at = serverNow - delayMs
  const all = [...(ix.live ? [ix.live] : []), ...ix.heats].filter(h => h.start <= at)
  const on = all.filter(h => h.end === null || h.end > at).sort((a, b) => b.start - a.start)
  return { live: on[0] ?? null, done: all.filter(h => h.end !== null && h.end <= at).sort((a, b) => b.start - a.start) }
}

// how a heat is called: "HEAT 7 · EXPERT", or its start time when the heat manager was not reachable
export const heatName = (h: { heat: string | null; cls: string | null; start: number }) =>
  h.heat ? `HEAT ${h.heat}${h.cls ? ' · ' + h.cls.toUpperCase() : ''}` : clock(h.start)
export const clock = (ms: number) => new Date(ms).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Tokyo' })
