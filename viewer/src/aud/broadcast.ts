// The event's broadcast sound beside a replay, in step with it: heats/<id>/broadcast.json {"audio": <file>, "t0": <ms>} names
// the heat's cut of the stream's audio track and the wall time its 0 s plays (tools/dvr/broadcast_audio.py). Our own file, not
// YouTube's player, which stayed still until it was clicked itself and could not tell the page it had been refused
export type Broadcast = { audio: string; t0: number }

// the sound keeps its own clock; it is moved only when it is SEEK s off, and not again within GAP ms (a seek is heard)
const SEEK = 0.25, GAP = 1000
export function createBroadcast(url: string, b: Broadcast, onBlocked: () => void) {
  const a = Object.assign(new Audio(url), { preload: 'auto' })
  let lastSeek = 0, lastPlay = 0
  return {
    // every frame: the sound at the replay's wall time (ms), playing or paused with it, at its speed
    follow(wall: number, playing: boolean, speed: number) {
      const want = (wall - b.t0) / 1000, now = performance.now()
      if (want < 0 || want > (a.duration || Infinity)) { if (!a.paused) a.pause(); return }
      if (a.playbackRate !== speed) a.playbackRate = speed
      const off = Math.abs(a.currentTime - want)
      if (playing) {
        if (off > SEEK && now - lastSeek > GAP) { a.currentTime = want; lastSeek = now }
        // a browser refuses sound before the page's first click (NotAllowedError): onBlocked, and the page turns it off
        if (a.paused && now - lastPlay > 500) { lastPlay = now; a.play().catch(e => { if (e?.name === 'NotAllowedError') onBlocked() }) }
      } else {
        if (!a.paused) a.pause()
        if (off > 0.05) a.currentTime = want
      }
    },
    get wall() { return a.currentTime * 1000 + b.t0 },   // the wall time (ms) the sound is at: the page's checks
    destroy() { a.pause(); a.removeAttribute('src'); a.load() },
  }
}
