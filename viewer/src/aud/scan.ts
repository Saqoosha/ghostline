// The scan a page shows (stage.ts for the audience pages, livepage.ts at the venue): lite (0.8M splats, 12 MB) unless the
// viewer asks for HQ (the full 3M, 45 MB; the two looked alike at 1.5M against 0.8M, apart at 3M), read through Cache Storage.
//   ?scan=full|mid|lite picks one to compare them (mid: <scene>-mid.sog, when one is up)
const Q = new URLSearchParams(typeof location === 'undefined' ? '' : location.search)
export const loadHQ = () => { try { return localStorage.getItem('ghostline-hq') === '1' } catch { return false } }   // this browser's choice
export const saveHQ = (on: boolean) => { try { localStorage.setItem('ghostline-hq', on ? '1' : '0') } catch { /* private window: not kept */ } }
export function pickScan(ix: { scene: string; lite?: string }, hq: boolean) {
  const q = Q.get('scan'); return q === 'full' ? ix.scene : q === 'mid' ? `${ix.scene}-mid` : q === 'lite' && ix.lite ? ix.lite : !hq && ix.lite ? ix.lite : ix.scene
}

// the scan (45 MB) kept in this browser's Cache Storage: the HTTP cache did not keep it, so every heat page took it again
// (30 s and more on a slow line). Kept by the server's etag, so a new scan under the same name is taken afresh; where Cache
// Storage is not there (a private window, plain http) it is only taken, as before
export async function sceneBlob(src: string, progress?: (f: number) => void): Promise<Blob> {
  const store = await (typeof caches !== 'undefined' ? caches.open('ghostline-scenes').catch(() => null) : null)
  const head = store ? await fetch(src, { method: 'HEAD' }).catch(() => null) : null, tag = head?.ok ? head.headers.get('etag') ?? '' : ''
  const key = `${src}?etag=${encodeURIComponent(tag)}`
  const hit = store && tag ? await store.match(key).catch(() => undefined) : undefined
  if (hit) { progress?.(1); return hit.blob() }
  const r = await fetch(src); if (!r.ok || !r.body) throw new Error(`${src}: ${r.status}`)
  const size = Number(r.headers.get('content-length')) || NaN, parts: Uint8Array[] = []; let got = 0
  for (const rd = r.body.getReader(); ;) { const { done, value } = await rd.read(); if (done) break; parts.push(value); got += value.length; progress?.(got / size) }
  const blob = new Blob(parts as BlobPart[])
  if (store && tag) {                                        // the old copies of this scan go, then this one is kept (not waited for)
    for (const k of await store.keys().catch(() => [] as readonly Request[])) if (k.url.startsWith(src + '?')) store.delete(k).catch(() => {})
    store.put(key, new Response(blob)).catch(e => console.warn('scene not kept', e))
  }
  return blob
}
