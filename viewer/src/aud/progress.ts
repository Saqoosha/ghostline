// The run's progress on the live audience page (watch.ts), drawn as the venue's 3D page draws its own (livepage.ts advance /
// drawRace): a row per pilot, a bar over the run's laps, the dot where the drone is along the course line (track.json),
// counted in laps from its holeshot, with the laps' lines, the end's, and tiny ticks where the course's other gates are.
// The page has no run picker, so the run is told from the holeshots: two or more within MASS s of each other is a race's
// mass start (4 laps, places, FIN); otherwise practice / qualifying (staggered starts): the class's laps (track.json
// "qual_laps", the most begun in 90 s that day) and each row's seconds left of its 90 s instead of a place.
export type Course = { line: number[][]; S: number[]; L: number; gates: number[]; qualLaps: Record<string, number> }
// hs, done, wraps (each time round past the line's end), dec (its transponder's passes counted): s on the heat's clock
export type Prog = { arc: number | null; lap: number; hs: number | null; done: number | null; wraps?: number[]; dec?: { n: number; last: number } | null }
export type ProgRow = { cell: string; name: string; color: string; prog: Prog }
export const RACE_LAPS = 4, RUN = 90, MASS = 2

const flat = (a: number[], b: number[]) => Math.hypot(a[0] - b[0], a[2] - b[2])
const nearest = (line: number[][], p: number[]) => { let k = 0, bd = Infinity; line.forEach((q, i) => { const d = flat(q, p); if (d < bd) { bd = d; k = i } }); return k }

export function makeCourse(j: any): Course | null {
  const line: number[][] = j?.track; if (!Array.isArray(line) || line.length < 2) return null
  const S = [0]; for (let k = 1; k < line.length; k++) S.push(S[k - 1] + Math.hypot(line[k][0] - line[k - 1][0], line[k][1] - line[k - 1][1], line[k][2] - line[k - 1][2]))
  const n = line.length, last = line[n - 1], L = S[n - 1] + Math.hypot(last[0] - line[0][0], last[1] - line[0][1], last[2] - line[0][2])
  return { line, S, L, gates: (j.gates ?? []).map((g: any) => S[nearest(line, g.centre)] / L), qualLaps: j.qual_laps ?? {} }
}
export const newProg = (): Prog => ({ arc: null, lap: 0, hs: null, done: null, wraps: [], dec: null })

// where on the line: looked for from a little behind the last place to 40 m on (the line crosses itself), over all of it
// when that finds nothing within 10 m. Round past the line's end is a lap; the first is the holeshot (the pads are off the end)
export function advance(c: Course, p: Prog, pos: number[], t: number) {
  const n = c.line.length; let best = -1, bd = Infinity
  if (p.arc !== null) for (let k = 0; k < n; k++) {
    let ds = c.S[k] - p.arc; if (ds < -c.L / 2) ds += c.L; if (ds > c.L / 2) ds -= c.L
    if (ds < -5 || ds > 40) continue; const d = flat(c.line[k], pos); if (d < bd) { bd = d; best = k }
  }
  if (best < 0 || bd > 10) best = nearest(c.line, pos)
  const s = c.S[best]
  if (p.arc !== null && p.arc > 0.7 * c.L && s < 0.3 * c.L) { p.lap++; p.wraps?.push(t); if (p.hs === null) p.hs = t }
  else if (p.arc !== null && p.arc < 0.3 * c.L && s > 0.7 * c.L && p.lap > 0) p.lap--
  p.arc = s
}
// with its transponder's passes the laps are the decoder's, the part of the one under way still the line's: just past the
// gate by the decoder and not yet round the line's end is the new lap's start, round the end with no pass yet the last's end
export function progressOf(c: Course, p: Prog, t = Infinity) {
  if (p.dec) {
    let f = p.arc === null ? 0 : p.arc / c.L; const w = p.wraps ?? [], rounded = w.length > 0 && w[w.length - 1] > p.dec.last + 3
    if (f > 0.75 && t - p.dec.last < 3) f = 0; else if (rounded && f < 0.25) f = 0.999
    return p.dec.n - 1 + f
  }
  return p.lap === 0 || p.arc === null ? 0 : p.lap - 1 + p.arc / c.L
}

// The decoder's passes (aud/delay.ts PMsg, put on the heat's clock; the caller gives this heat's): each transponder goes to
// the pilot whose own rounding of the line's end comes nearest its passes (within MATCH s), the most such first, afresh each
// frame (as livepage.ts matchPasses does with its gate crossings). A matched pilot's laps run from its transponder's first
// pass (the holeshot: the line often misses it in the mass start's crowd), and a pass the decoder missed (JDL R7 h17: one
// lap of 40 s between laps of 18) is filled by the line's rounding there - one farther than 0.6 of a lap from every pass.
// A pass with few hits brushed past the loop; one sooner than MIN_LAP after the last is the same again (a crash at the gate)
export type Pass = { t: number; tx: number; hits?: number }
const MATCH = 1.5, CLEAR = 0.5, MIN_HITS = 10, MIN_LAP = 8
export function decode(progs: Map<string, Prog>, passes: Pass[], t: number) {
  const ps = passes.filter(x => x.t <= t && (x.hits ?? 99) >= MIN_HITS), votes = new Map<string, number>()
  for (const x of ps) {
    // the nearest pilot's, when the next is CLEAR s farther (a close race rounds within a second of each other, JDL R7 h19)
    const d = [...progs].map(([cell, p]) => [cell, Math.min(Infinity, ...(p.wraps ?? []).map(w => Math.abs(w - x.t)))] as [string, number]).sort((a, b) => a[1] - b[1])
    if (!d.length || d[0][1] >= MATCH || (d.length > 1 && d[1][1] - d[0][1] < CLEAR)) continue
    const k = `${x.tx}|${d[0][0]}`; votes.set(k, (votes.get(k) ?? 0) + 1)
  }
  const of = new Map<string, number>(), used = new Set<number>()
  for (const [k] of [...votes].sort((a, b) => b[1] - a[1])) {
    const tx = Number(k.slice(0, k.indexOf('|'))), cell = k.slice(k.indexOf('|') + 1)
    if (!of.has(cell) && !used.has(tx)) { of.set(cell, tx); used.add(tx) }
  }
  for (const [cell, p] of progs) {
    const tx = of.get(cell); p.dec = null; if (tx === undefined) continue
    const mine: number[] = []; for (const x of ps) if (x.tx === tx && (!mine.length || x.t - mine[mine.length - 1] >= MIN_LAP)) mine.push(x.t)
    if (!mine.length) continue
    const laps = mine.slice(1).map((x, k) => x - mine[k]).sort((a, b) => a - b), far = 0.6 * (laps.length ? laps[laps.length >> 1] : 15)
    const fill = (p.wraps ?? []).filter(w => w > mine[0] && w <= t && mine.every(x => Math.abs(w - x) > far))
    const all = [...mine, ...fill].sort((a, b) => a - b), xs: number[] = []
    for (const x of all) if (!xs.length || x - xs[xs.length - 1] >= MIN_LAP) xs.push(x)
    p.dec = { n: xs.length, last: xs[xs.length - 1] }
  }
  return of
}
export function isRace(ps: Prog[]) { const h = ps.flatMap(p => p.hs === null ? [] : [p.hs]); return h.length >= 2 && Math.max(...h) - Math.min(...h) <= MASS }

// draws the rows at t (s on the heat's clock), four rows tall whoever flies; returns the canvas's CSS height
// opt.laps / opt.places: set by the caller instead of told from the holeshots (compare.ts: every row's holeshot is put at one time)
export function drawProgress(cv: HTMLCanvasElement, c: Course, rows: ProgRow[], t: number, cls: string | null, opt: { laps?: number; places?: boolean } = {}): number {
  const W = cv.clientWidth, ROW = 26, H = 4 * ROW + 6, r = window.devicePixelRatio || 1
  if (cv.width !== Math.round(W * r) || cv.height !== Math.round(H * r)) { cv.width = Math.round(W * r); cv.height = Math.round(H * r); cv.style.height = H + 'px' }
  const g = cv.getContext('2d')!; g.setTransform(r, 0, 0, r, 0, 0); g.clearRect(0, 0, W, H)
  if (!rows.length || !W) return H
  const race = opt.places ?? isRace(rows.map(x => x.prog)), ql = Object.values(c.qualLaps)
  const N = opt.laps ?? (race ? RACE_LAPS : c.qualLaps[cls ?? ''] ?? (ql.length ? Math.max(...ql) : RACE_LAPS))
  const prog = new Map(rows.map(x => { const v = progressOf(c, x.prog, t); if (race && v >= N && x.prog.done === null) x.prog.done = t; return [x, x.prog.done !== null && race ? N : v] as [ProgRow, number] }))
  const order = [...rows].sort((a, b) => (a.prog.done ?? Infinity) - (b.prog.done ?? Infinity) || prog.get(b)! - prog.get(a)!), place = new Map(order.map((x, i) => [x, i + 1]))
  const label = (x: ProgRow) => (race ? `P${place.get(x)}  ` : '') + (x.name || x.cell)
  g.font = '500 12px "Helvetica Neue", "Hiragino Sans", sans-serif'; g.textBaseline = 'middle'
  const x0 = Math.max(48, ...rows.map(x => g.measureText(label(x)).width + 18)), x1 = W - (race ? 34 : 64), X = (v: number) => x0 + Math.min(1, Math.max(0, v / N)) * (x1 - x0)
  rows.forEach((x, k) => {
    const y = 3 + k * ROW + ROW / 2, v = prog.get(x)!
    g.fillStyle = x.color; g.fillRect(0, y - 7, 3, 14)
    g.fillStyle = '#0c0f14'; g.font = '500 12px "Helvetica Neue", "Hiragino Sans", sans-serif'; g.fillText(label(x), 9, y)
    g.fillStyle = 'rgba(12, 15, 20, .1)'; g.fillRect(x0, y - 2, x1 - x0, 4)
    g.fillStyle = 'rgba(12, 15, 20, .35)'; for (let i = 1; i <= N; i++) g.fillRect(X(i) - 0.5, y - 8, 1, 16)   // the laps, and the end
    g.fillStyle = x.color; g.fillRect(x0, y - 2, X(v) - x0, 4)
    g.fillStyle = 'rgba(12, 15, 20, .3)'; for (let i = 0; i < N; i++) for (const f of c.gates) g.fillRect(X(i + f) - 0.5, y - 5, 1, 3)   // the gates
    g.beginPath(); g.arc(X(v), y, 5, 0, Math.PI * 2); g.fillStyle = x.color; g.fill(); g.strokeStyle = '#fff'; g.lineWidth = 1.5; g.stroke()
    const left = !race && x.prog.hs !== null ? Math.ceil(RUN - (t - x.prog.hs)) : null
    g.fillStyle = '#0c0f14'; g.font = '500 11px ui-monospace, "SF Mono", Menlo, monospace'
    g.fillText(race && x.prog.done !== null ? 'FIN' : v > 0 ? `L${Math.min(N, Math.floor(v) + 1)}` + (left !== null && left > 0 ? ` ${left}s` : '') : '', x1 + 6, y)
  })
  return H
}
