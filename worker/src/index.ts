/**
 * ghostline.saqoo.sh - DVR flight viewers, served from R2.
 *
 * /<name>/<path> is the object dvr/<name>/<path>: the built page (index.html, race.html, assets/, flights.json) and
 * its data (data/...: scenes, videos, poses) side by side, uploaded by tools/publish-dvr-viewer.sh. / lists the viewers.
 * A race day's audience pages (tools/publish-event.sh) live the same way; /<event>/api/* is its live relay (room.ts).
 */
import { authorized, heatFile, parseEventPath } from './api'
export { LiveRoom } from './room'

export interface Env {
  BUCKET: R2Bucket
  ROOM: DurableObjectNamespace
  RELAY_KEY: string
}
const PREFIX = 'dvr/'

// /<event>/api/live: a viewer's WebSocket; /<event>/api/push and PUT /<event>/api/heats/<id>: the venue's sender, with the key
function relay(request: Request, env: Env, event: string, rest: string): Promise<Response> | Response {
  if (rest !== 'live' && !authorized(request, env.RELAY_KEY)) return new Response('unauthorized', { status: 401 })
  const f = heatFile(rest)                                  // a heat's clip or race.json: straight to R2, the room has no part in it
  if (f && request.method === 'PUT') return putHeatFile(request, env, event, f)
  const headers = new Headers(request.headers); headers.set('x-event', event); headers.set('x-rest', rest)
  return env.ROOM.get(env.ROOM.idFromName(event)).fetch(new Request(request, { headers }))
}

async function putHeatFile(request: Request, env: Env, event: string, f: { id: string; file: string; type: string }): Promise<Response> {
  if (!request.body || !Number(request.headers.get('content-length'))) return new Response('empty body', { status: 400 })
  await env.BUCKET.put(`${PREFIX}${event}/heats/${f.id}/${f.file}`, request.body, { httpMetadata: { contentType: f.type } })
  return new Response(null, { status: 204 })
}

function cacheFor(key: string): string {
  // pages and json are replaced under the same name on every publish; Vite's assets/ carry a content hash
  if (/\.(html|json)$/.test(key)) return 'no-cache'
  if (/\/assets\//.test(key)) return 'public, max-age=31536000, immutable'
  return 'public, max-age=86400'
}

async function listing(env: Env): Promise<Response> {
  const l = await env.BUCKET.list({ prefix: PREFIX, delimiter: '/' })
  const names = l.delimitedPrefixes.map(p => p.slice(PREFIX.length, -1)).filter(n => !n.startsWith('staging-'))   // test days stay unlisted
  const esc = (s: string) => s.replace(/[&<>"]/g, c => `&#${c.charCodeAt(0)};`)
  const items = names.map(n => `<li><a href="/${encodeURIComponent(n)}/">${esc(n)}</a></li>`).join('')
  return new Response(`<!doctype html><meta charset="utf-8"><title>ghostline</title><h1>ghostline</h1><ul>${items}</ul>`,
    { headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-cache' } })
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const api = parseEventPath(new URL(request.url).pathname)
    if (api) return relay(request, env, api.event, api.rest)
    if (request.method !== 'GET' && request.method !== 'HEAD')
      return new Response('method not allowed', { status: 405, headers: { Allow: 'GET, HEAD' } })
    const url = new URL(request.url)
    if (url.pathname === '/') return listing(env)
    const path = decodeURIComponent(url.pathname.slice(1))
    if (path.split('/').includes('..')) return new Response('bad path', { status: 400 })
    let key = PREFIX + (path.endsWith('/') ? path + 'index.html' : path)

    let head = await env.BUCKET.head(key)
    if (head === null && !path.endsWith('/') && !/\.[^/]+$/.test(path)) {
      // /name -> /name/ (a viewer's folder), /name/race -> race.html
      if (await env.BUCKET.head(key + '/index.html')) return Response.redirect(new URL(url.pathname + '/' + url.search, url).toString(), 301)
      if ((head = await env.BUCKET.head(key + '.html'))) key += '.html'
    }
    if (head === null) return new Response('not found', { status: 404 })

    // Range matters: big videos and scenes over links that drop; a browser that cannot resume starts from zero
    const object = await env.BUCKET.get(key, { onlyIf: request.headers, range: request.headers })
    if (object === null) return new Response('not found', { status: 404 })
    const headers = new Headers()
    object.writeHttpMetadata(headers)
    headers.set('etag', object.httpEtag)
    headers.set('accept-ranges', 'bytes')
    headers.set('cache-control', cacheFor(key))
    if (!('body' in object)) return new Response(null, { status: 304, headers })
    if (request.headers.has('range') && object.range && 'offset' in object.range) {   // R2 fills range in on a plain GET too
      const start = object.range.offset ?? 0
      const length = object.range.length ?? object.size - start
      headers.set('content-range', `bytes ${start}-${start + length - 1}/${object.size}`)
      return new Response(request.method === 'HEAD' ? null : object.body, { status: 206, headers })
    }
    // stored gzipped (a heat's live.json): sent as it is, or the runtime gzips it a second time
    const encodeBody = object.httpMetadata?.contentEncoding ? 'manual' : 'automatic'
    return new Response(request.method === 'HEAD' ? null : object.body, { headers, encodeBody })
  },
}
