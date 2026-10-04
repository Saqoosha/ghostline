// What a live page would draw from the tracker's answers as they arrive (rt_track.py's answer log, replayed at the
// time each answer was finished). The drawing is done here, not in the tracker: live, the page receives the answers
// as they are solved (livepage.ts) and decides how to show them.
//   dot   'extrap': a quadratic over the newest answers, carried to the present frame (no added delay)
//         'delay' : the frame `delay` seconds ago, fitted between the answers on both sides of it; when no answer after
//                   it has arrived yet, or the answers around it are too far apart (tracking was lost), the dot
//                   holds at the answer before it, and after `lostAfter` seconds it is shown as lost
//   trail every frame's position refitted from all answers that have arrived, so a guess is corrected as soon as the
//         next answer lands; frames older than `settle` seconds are left as they are
export type Answer = { i: number; done: number; pos: number[]; quat: number[]; inl: number; how: string }
export type Dot = { pos: number[]; quat: number[]; state: 'interp' | 'extrap' | 'hold' | 'lost'; frame: number }


// taper: weights fall to zero at the window's edge (biweight) instead of ending at a step - an answer entering or
// leaving a box window moves the fit at once, which shows as small kinks along a trail
function fit(ans: Answer[], j: number, W: number, taper = false): number[] | null {   // weighted quadratic through the answers within W frames of j, at j
  let s0 = 0, s1 = 0, s2 = 0, s3 = 0, s4 = 0; const b0 = [0, 0, 0], b1 = [0, 0, 0], b2 = [0, 0, 0]; let n = 0
  for (const a of ans) {
    const x = a.i - j; if (Math.abs(x) > W) continue
    const u = x / (W + 1), w = Math.sqrt(Math.min(a.inl, 400) / 200) * (taper ? (1 - u * u) ** 2 : 1); n++
    s0 += w; s1 += w * x; s2 += w * x * x; s3 += w * x * x * x; s4 += w * x * x * x * x
    for (let k = 0; k < 3; k++) { b0[k] += w * a.pos[k]; b1[k] += w * x * a.pos[k]; b2[k] += w * x * x * a.pos[k] }
  }
  if (n < 4) return null
  // [s0 s1 s2; s1 s2 s3; s2 s3 s4] c = b, value at x = 0 is c0 (Cramer)
  const det = s0 * (s2 * s4 - s3 * s3) - s1 * (s1 * s4 - s3 * s2) + s2 * (s1 * s3 - s2 * s2)
  if (Math.abs(det) < 1e-9) return null
  return [0, 1, 2].map(k => (b0[k] * (s2 * s4 - s3 * s3) - s1 * (b1[k] * s4 - s3 * b2[k]) + s2 * (b1[k] * s3 - s2 * b2[k])) / det)
}
function slerp(a: number[], b: number[], t: number): number[] {
  let d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]; const s = d < 0 ? -1 : 1; d *= s
  if (d > 0.9995) { const q = a.map((v, k) => v + (s * b[k] - v) * t); const n = Math.hypot(...q); return q.map(v => v / n) }
  const th = Math.acos(d), sa = Math.sin((1 - t) * th) / Math.sin(th), sb = Math.sin(t * th) / Math.sin(th)
  return a.map((v, k) => sa * v + sb * s * b[k])
}

export class LiveDraw {
  byDone: Answer[]; fps: number; trail: (number[] | null)[] = []; trailT = -1
  ws = 0.25                                           // seconds either side in a fit
  taper = false
  constructor(answers: Answer[], fps: number) { this.byDone = answers.slice().sort((a, b) => a.done - b.done); this.fps = fps }
  arrived(t: number): Answer[] {                      // answers finished by time t, in frame order
    let lo = 0, hi = this.byDone.length
    while (lo < hi) { const m = (lo + hi) >> 1; if (this.byDone[m].done <= t) lo = m + 1; else hi = m }
    return this.byDone.slice(0, lo).sort((a, b) => a.i - b.i)
  }
  dot(t: number, mode: 'extrap' | 'delay', delay: number, lostAfter = 0.3): Dot | null {
    const got = this.arrived(t); if (!got.length) return null
    const W = this.ws * this.fps
    const now = Math.floor(t * this.fps + 1e-3), last = got[got.length - 1]
    if (mode === 'extrap') {
      const recent = got.filter(a => a.i > now - 2 * W)
      const pos = (recent.length >= 4 ? fit(recent, now, W, this.taper) : null) ?? last.pos
      return { pos, quat: last.quat, state: now - last.i > lostAfter * this.fps ? 'lost' : 'extrap', frame: now }
    }
    const j = Math.floor((t - delay) * this.fps + 1e-3)
    let before: Answer | null = null, after: Answer | null = null
    for (const a of got) { if (a.i <= j) before = a; if (a.i >= j && !after) after = a }
    if (before && after && after.i - before.i <= 2 * W) {
      const pos = fit(got, j, W, this.taper) ?? before.pos, u = after.i === before.i ? 0 : (j - before.i) / (after.i - before.i)
      return { pos, quat: slerp(before.quat, after.quat, u), state: 'interp', frame: j }
    }
    // no answer after frame j yet, or a gap around it: hold at the answer before j (never at one ahead of it)
    if (!before) return null                          // before the first answer
    return { pos: before.pos, quat: before.quat, state: (j - before.i) / this.fps > lostAfter ? 'lost' : 'hold', frame: before.i }
  }
  trailAt(t: number, upto: number, settle = 0.5): (number[] | null)[] {
    if (t < this.trailT) this.trail = []              // seeked back: start again
    this.trailT = t
    const got = this.arrived(t), from = Math.max(0, upto - Math.ceil(settle * this.fps)), W = this.ws * this.fps
    for (let k = this.trail.length; k < from; k++) this.trail[k] = fit(got.filter(a => Math.abs(a.i - k) <= W), k, W, this.taper)
    for (let k = from; k <= upto; k++) this.trail[k] = fit(got.filter(a => Math.abs(a.i - k) <= W), k, W, this.taper)
    this.trail.length = upto + 1
    return this.trail
  }
}
