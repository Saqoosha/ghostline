// One race day's live relay (a Durable Object per event name). The venue posts its Msgs here once; every viewer's
// WebSocket gets them from here, so the venue's battery and uplink carry one stream however many watch.
// It keeps the last KEEP of Msgs for a viewer who just connected (a delayed viewer draws at once), the last heat start
// (in storage: it outlives the object sleeping), and writes the day's heat list (dvr/<event>/heats.json) when a heat
// starts, is renamed or ends, and when its record is uploaded - a few writes per heat.
import { DurableObject } from 'cloudflare:workers'
import { applyToIndex, HEAT_ID, validEntry, type HeatIndex, type HMsg, type Msg } from './api'

export interface RoomEnv { BUCKET: R2Bucket }
const KEEP = 80_000                                      // 60 s of delay, the page's 0.7 s lag, one 10 s repeat of the heat start, and margin

export class LiveRoom extends DurableObject<RoomEnv> {
  recent: Msg[] = []
  lastH: HMsg | null | undefined                            // undefined: not read from storage yet
  ended = new Set<string>()

  async fetch(req: Request): Promise<Response> {
    const event = req.headers.get('x-event') ?? '', rest = req.headers.get('x-rest') ?? ''
    if (rest === 'live') {
      if (req.headers.get('upgrade') !== 'websocket') return new Response('websocket only', { status: 426 })
      const [client, server] = Object.values(new WebSocketPair())
      this.ctx.acceptWebSocket(server)
      server.send(JSON.stringify({ now: Date.now(), backlog: this.recent, heat: await this.heat() }))
      return new Response(null, { status: 101, webSocket: client })
    }
    if (rest === 'push' && req.method === 'POST') {
      const body = await req.json<{ msgs?: Msg[] }>().catch(() => null)
      if (!body || !Array.isArray(body.msgs)) return new Response('bad body', { status: 400 })
      await this.push(event, body.msgs)
      return new Response(null, { status: 204 })
    }
    const m = /^heats\/(.+)$/.exec(rest)
    if (m && req.method === 'PUT') {
      if (!HEAT_ID.test(m[1])) return new Response('bad heat id', { status: 400 })
      // the heat's list entry rides along: the push carrying its end may have been dropped on the venue's uplink
      let entry: unknown
      try { entry = JSON.parse(req.headers.get('x-heat-entry') ?? '') } catch { return new Response('x-heat-entry missing or not JSON', { status: 400 }) }
      if (!validEntry(entry, m[1])) return new Response('x-heat-entry is not this heat ended (id, start, end, pilots)', { status: 400 })
      await this.env.BUCKET.put(`dvr/${event}/heats/${m[1]}/live.json`, req.body,
        { httpMetadata: { contentType: 'application/json', contentEncoding: 'gzip' } })
      await this.index(event, ix => ({ ...ix, live: ix.live?.id === entry.id ? null : ix.live, heats: [entry, ...ix.heats.filter(h => h.id !== entry.id)].sort((a, b) => b.start - a.start) }))
      // still live here: the end's push was dropped. It ends now, for new viewers and for those watching
      if ((await this.heat())?.id === entry.id) await this.push(event, [{ k: 'e', t: entry.end, id: entry.id }])
      return new Response(null, { status: 204 })
    }
    return new Response('not found', { status: 404 })
  }

  async heat(): Promise<HMsg | null> {
    if (this.lastH === undefined) this.lastH = (await this.ctx.storage.get<HMsg>('lastH')) ?? null
    return this.lastH
  }

  async push(event: string, msgs: Msg[]) {
    const now = Date.now()
    // a heat start's t is the heat's start; when it was sent ("at") is what ages it. An end takes its heat's starts out of
    // the backlog: the end (aged by its t, the last row) would leave it first, and a new viewer would see the heat as live
    const gone = new Set(msgs.filter(x => x.k === 'e').map(x => (x as { id: string }).id))
    this.recent.push(...msgs); this.recent = this.recent.filter(x => (x.k === 'h' ? x.at ?? x.t : x.t) > now - KEEP && !(x.k === 'h' && gone.has(x.id)))
    const sockets = this.ctx.getWebSockets()
    if (sockets.length && msgs.length) { const s = JSON.stringify({ now, msgs }); for (const ws of sockets) { try { ws.send(s) } catch { /* closing */ } } }
    for (const x of msgs) {
      if (x.k !== 'h' && x.k !== 'e') continue
      const was = await this.heat()
      if (x.k === 'h' && this.ended.has(x.id)) continue                // a late start of a heat already ended
      if (x.k === 'e') this.ended.add(x.id)
      // the 10 s repeat of an unchanged start: nothing to store or write
      if (x.k === 'h' && was?.id === x.id && was.heat === x.heat && was.cls === x.cls && JSON.stringify(was.cells) === JSON.stringify(x.cells)) continue
      if (x.k === 'h') { this.lastH = x; await this.ctx.storage.put('lastH', x) }
      else if (was?.id === x.id) { this.lastH = null; await this.ctx.storage.delete('lastH') }
      await this.index(event, ix => applyToIndex(ix, x))
    }
  }

  // read, change, write back only when it changed (the sender repeats the heat start every 10 s)
  // one at a time: an R2 await lets the next request in, and a push and a heat upload interleaving lost one's change
  chain: Promise<void> = Promise.resolve()
  index(event: string, f: (ix: HeatIndex) => HeatIndex): Promise<void> {
    const run = this.chain.then(() => this.indexNow(event, f)); this.chain = run.catch(() => {}); return run
  }
  async indexNow(event: string, f: (ix: HeatIndex) => HeatIndex) {
    const key = `dvr/${event}/heats.json`, o = await this.env.BUCKET.get(key)
    const ix: HeatIndex = o ? await o.json() : { event, title: event, live: null, heats: [] }
    const next = f(ix)
    if (JSON.stringify(next) !== JSON.stringify(ix))
      await this.env.BUCKET.put(key, JSON.stringify(next), { httpMetadata: { contentType: 'application/json' } })
  }

  webSocketMessage() { /* viewers only listen */ }
  webSocketClose(ws: WebSocket, code: number) { try { ws.close(code === 1005 ? 1000 : code) } catch { /* gone */ } }
}
