// The lap timeline of a replay (heat.ts), drawn as the venue's 3D page draws its own (livepage.ts drawTimeline): a row per
// pilot from a little before the heat's first holeshot, each lap a block in the pilot's colour, strong and light by turns,
// HS at the holeshot and L<n> <time> at each pass, a dashed line 90 s after the pilot's own holeshot when the axis reaches it, and
// the playhead. The passes are the official ones: the venue's timing (Speedhive) put into race.json / live.json as
// "official" by tools/dvr/official_laps.py. A lap the decoder missed a pass in is one long lap there; it is labelled with
// the laps it holds (L2-4), counted against the pilot's typical lap.
export type Official = { session: string; cells: Record<string, { name: string; laps: number[]; ends: number[]; holeshot: number; laps_driven: number; best?: string }> }
export type LapRow = { cell: string; name: string; color: string; hs: number; ends: number[]; laps: number[]; num: number[] }   // times in seconds from the replay's start; num: the lap count at each pass
export const RACE = 90

export function lapRows(off: Official | undefined, start: number, colorOf: (cell: string) => string, order: string[]): LapRow[] {
  if (!off) return []
  return Object.entries(off.cells)
    .sort(([a], [b]) => ((order.indexOf(a) + 9) % 9) - ((order.indexOf(b) + 9) % 9))
    .map(([cell, c]) => ({ cell, name: c.name, color: colorOf(cell), hs: (c.holeshot - start) / 1000, ends: c.ends.map(e => (e - start) / 1000), laps: c.laps, num: lapNumbers(c.laps) }))
}

// a lap the decoder missed a pass in holds as many laps as it is long against the pilot's typical (median) lap
function lapNumbers(laps: number[]): number[] {
  const typical = [...laps].sort((a, b) => a - b)[Math.floor(laps.length / 2)] ?? 1
  let n = 0; return laps.map(l => (n += Math.max(1, Math.round(l / typical))))
}

// the standings at t, from the passes made by then: more laps first, then the earlier last pass. A lap holding several
// (a missed pass) is no one's best. Nothing of what is still to come shows: the replay does not give the result away
export type Standing = { pos: number; laps: number; time: number | null; best: number | null }
export function standings(rows: LapRow[], t: number): Standing[] {
  const st = rows.map(r => {
    const k = r.ends.filter(e => e <= t).length, laps = k ? r.num[k - 1] : 0
    const single = r.laps.slice(0, k).filter((_, i) => r.num[i] - (r.num[i - 1] ?? 0) === 1)
    return { pos: 0, laps, last: k ? r.ends[k - 1] : Infinity, time: k ? r.ends[k - 1] - r.hs : null, best: single.length ? Math.min(...single) : null }
  })
  st.map((s, i) => i).sort((a, b) => st[b].laps - st[a].laps || st[a].last - st[b].last).forEach((i, k) => { st[i].pos = st[i].laps ? k + 1 : 0 })
  return st.map(({ pos, laps, time, best }) => ({ pos, laps, time, best }))
}

const span = (rows: LapRow[]) => {
  const t0 = Math.min(...rows.map(r => r.hs)) - 3
  // to the last pass: a race of laps (JDL, 4 laps in about 47 s) left half the axis empty when it ran to 90 s; with no pass, the 90 s
  const last = Math.max(...rows.map(r => r.ends[r.ends.length - 1] ?? -Infinity))
  return [t0, (Number.isFinite(last) ? last : Math.max(...rows.map(r => r.hs + RACE))) + 3]
}
const NAME = (w: number) => w < 600 ? 104 : 170
const STAT = (w: number) => w < 600 ? 56 : 210         // the standings, right of the axis
const axis = (w: number) => [NAME(w), w - 4 - STAT(w)]
const FASTEST = '#6b2fd6'                                  // the heat's fastest lap so far

// draws the rows at time t; returns the canvas's CSS height
export function drawLaps(c: HTMLCanvasElement, rows: LapRow[], t: number): number {
  const W = c.clientWidth, ROW = 26, H = rows.length * ROW + 6, r = window.devicePixelRatio || 1
  if (c.width !== Math.round(W * r) || c.height !== Math.round(H * r)) { c.width = Math.round(W * r); c.height = Math.round(H * r); c.style.height = H + 'px' }
  const g = c.getContext('2d')!; g.setTransform(r, 0, 0, r, 0, 0); g.clearRect(0, 0, W, H)
  if (!rows.length || !W) return H
  const [t0, t1] = span(rows), [x0, x1] = axis(W), X = (s: number) => x0 + Math.min(1, Math.max(0, (s - t0) / (t1 - t0))) * (x1 - x0)
  const small = W < 600, st = standings(rows, t), bests = st.map(s => s.best ?? Infinity), fastest = Math.min(...bests)
  g.textBaseline = 'middle'
  rows.forEach((row, k) => {
    const y = 3 + k * ROW + ROW / 2
    g.fillStyle = row.color; g.fillRect(0, y - 8, 4, 16)
    g.fillStyle = '#0c0f14'; g.font = `700 ${small ? 11 : 13}px "Helvetica Neue", "Hiragino Sans", sans-serif`; g.fillText(`${row.cell}  ${row.name}`, 10, y, x0 - 16)
    g.fillStyle = 'rgba(12, 15, 20, .1)'; g.fillRect(x0, y + 3, x1 - x0, 5)
    // the laps, strong and light by turns; only as far as the playhead
    const marks = [row.hs, ...row.ends]
    g.fillStyle = row.color
    for (let i = 0; i + 1 < marks.length && marks[i] < t; i++) { g.globalAlpha = i % 2 ? 0.4 : 1; g.fillRect(X(marks[i]), y + 1, X(Math.min(marks[i + 1], t)) - X(marks[i]), 9) }
    g.globalAlpha = 1
    if (row.hs + RACE <= t1) { const x = X(row.hs + RACE); g.fillStyle = 'rgba(12, 15, 20, .55)'; for (let yy = y - 11; yy < y + 11; yy += 5) g.fillRect(x - 0.5, yy, 1, 3) }   // its 90 s, dashed
    // the passes: HS, then each lap's number(s) and time - past ones dark, ones to come faint
    g.font = `400 ${small ? 10 : 12}px ui-monospace, "SF Mono", Menlo, monospace`
    // a label goes right of its line (left of it at the right edge) and only where it does not run into the one before: the
    // line stays. A phone (under 600 px) gets the time alone, to 1/100 s, and no labels for the laps still to come
    // the lines first, then the labels over them on a paper ground, so the next pass's line does not cross the text
    let free = -Infinity; const texts: [string, number, boolean, boolean][] = []
    const label = (x: number, s: string, done: boolean, fast = false) => {
      if (small && !done) return
      const tw = g.measureText(s).width, lx = x + 4 + tw > x1 ? x - 4 - tw : x + 4
      if (lx < free) return
      texts.push([s, lx, done, fast]); free = lx + tw + 6
    }
    { const x = X(row.hs), done = row.hs <= t; g.fillStyle = done ? '#a3161a' : 'rgba(163, 22, 26, .3)'; g.fillRect(x - 0.5, y - 10, 1, 20); label(x, 'HS', done) }
    row.ends.forEach((e, i) => {
      const lap = row.laps[i], n = row.num[i], a = (row.num[i - 1] ?? 0) + 1
      const x = X(e), done = e <= t; g.fillStyle = done ? '#a3161a' : 'rgba(163, 22, 26, .3)'; g.fillRect(x - 1, y - 10, 2, 20)
      label(x, small ? lap.toFixed(2) : `L${n > a ? `${a}-${n}` : n} ${lap.toFixed(3)}`, done, done && a === n && lap === fastest)
    })
    const fs = small ? 10 : 12
    for (const [s, lx, done, fast] of texts) {
      const tw = g.measureText(s).width; g.fillStyle = 'rgba(243, 244, 246, .9)'; g.fillRect(lx - 2, y - 4 - fs / 2 - 1, tw + 4, fs + 2)
      g.fillStyle = fast ? FASTEST : done ? '#0c0f14' : 'rgba(12, 15, 20, .35)'; g.fillText(s, lx, y - 4)
    }
    // the standings at the playhead: place, laps, time since the holeshot, best lap (a phone: place and laps)
    const s = st[k], sx = x1 + 14
    if (s.pos) {
      g.font = `700 ${fs + 1}px ui-monospace, "SF Mono", Menlo, monospace`; g.fillStyle = '#0c0f14'; g.fillText(`P${s.pos}`, sx, y)
      g.font = `400 ${fs}px ui-monospace, "SF Mono", Menlo, monospace`; g.fillText(`${s.laps}L`, sx + 26, y)
      if (!small) {
        g.fillText(s.time!.toFixed(2), sx + 56, y)
        if (s.best !== null) { g.fillStyle = s.best === fastest ? FASTEST : 'rgba(12, 15, 20, .6)'; g.fillText(s.best.toFixed(3), sx + 130, y) }
      }
    }
  })
  if (t >= t0 && t <= t1) { g.fillStyle = '#a3161a'; g.fillRect(X(t) - 1, 0, 2, H) }   // the playhead
  return H
}

// the replay time under x on the canvas (CSS px), or null left of the axis: a click seeks there
export function timeAt(c: HTMLCanvasElement, rows: LapRow[], x: number): number | null {
  if (!rows.length) return null
  const [t0, t1] = span(rows), [x0, x1] = axis(c.clientWidth)
  return x < x0 || x > x1 ? null : t0 + Math.min(1, (x - x0) / (x1 - x0)) * (t1 - t0)
}
