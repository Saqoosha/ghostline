/**
 * ghostline.saqoo.sh - DVR flight viewers, served from R2.
 *
 * /<name>/<path> is the object dvr/<name>/<path>: the built page (index.html, race.html, assets/, flights.json) and
 * its data (data/...: scenes, videos, poses) side by side, uploaded by tools/publish-dvr-viewer.sh. / lists the viewers.
 */
export interface Env {
  BUCKET: R2Bucket
}
const PREFIX = 'dvr/'

function cacheFor(key: string): string {
  // pages and json are replaced under the same name on every publish; Vite's assets/ carry a content hash
  if (/\.(html|json)$/.test(key)) return 'no-cache'
  if (/\/assets\//.test(key)) return 'public, max-age=31536000, immutable'
  return 'public, max-age=86400'
}

async function listing(env: Env): Promise<Response> {
  const l = await env.BUCKET.list({ prefix: PREFIX, delimiter: '/' })
  const names = l.delimitedPrefixes.map(p => p.slice(PREFIX.length, -1))
  const esc = (s: string) => s.replace(/[&<>"]/g, c => `&#${c.charCodeAt(0)};`)
  const items = names.map(n => `<li><a href="/${encodeURIComponent(n)}/">${esc(n)}</a></li>`).join('')
  return new Response(`<!doctype html><meta charset="utf-8"><title>ghostline</title><h1>ghostline</h1><ul>${items}</ul>`,
    { headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-cache' } })
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
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
    if (object.range && 'offset' in object.range) {
      const start = object.range.offset ?? 0
      const length = object.range.length ?? object.size - start
      headers.set('content-range', `bytes ${start}-${start + length - 1}/${object.size}`)
      return new Response(request.method === 'HEAD' ? null : object.body, { status: 206, headers })
    }
    return new Response(request.method === 'HEAD' ? null : object.body, { headers })
  },
}
