// The lap timeline of a replay (heat.ts), drawn as the venue's 3D page draws its own (livepage.ts drawTimeline): a row per
// pilot from a little before the heat's first holeshot, each lap a block in the pilot's colour, strong and light by turns,
// HS at the holeshot and L<n> <time> at each pass, a dashed line 90 s after the pilot's own holeshot (staggered starts), and
// the playhead. The passes are the official ones: the venue's timing (Speedhive) put into race.json / live.json as
// "official" by tools/dvr/official_laps.py. A lap the decoder missed a pass in is one long lap there; it is labelled with
// the laps it holds (L2-4), counted against the pilot's typical lap.
export type Official = { session: string; cells: Record<string, { name: string; laps: number[]; ends: number[]; holeshot: number; laps_driven: number; best?: string }> }
export type LapRow = { cell: string; name: string; color: string; hs: number; ends: number[]; laps: number[] }   // times in seconds from the replay's start
export const RACE = 90

export function lapRows(off: Official | undefined, start: number, colorOf: (cell: string) => string, order: string[]): LapRow[] {
  if (!off) return []
  return Object.entries(off.cells)
    .sort(([a], [b]) => ((order.indexOf(a) + 9) % 9) - ((order.indexOf(b) + 9) % 9))
    .map(([cell, c]) => ({ cell, name: c.name, color: colorOf(cell), hs: (c.holeshot - start) / 1000, ends: c.ends.map(e => (e - start) / 1000), laps: c.laps }))
}

const span = (rows: LapRow[]) => {
  const t0 = Math.min(...rows.map(r => r.hs)) - 3
  return [t0, Math.max(...rows.map(r => Math.max(r.hs + RACE, r.ends[r.ends.length - 1] ?? r.hs))) + 3]
}
const NAME = (w: number) => w < 600 ? 104 : 170

// draws the rows at time t; returns the canvas's CSS height
export function drawLaps(c: HTMLCanvasElement, rows: LapRow[], t: number): number {
  const W = c.clientWidth, ROW = 26, H = rows.length * ROW + 6, r = window.devicePixelRatio || 1
  if (c.width !== Math.round(W * r) || c.height !== Math.round(H * r)) { c.width = Math.round(W * r); c.height = Math.round(H * r); c.style.height = H + 'px' }
  const g = c.getContext('2d')!; g.setTransform(r, 0, 0, r, 0, 0); g.clearRect(0, 0, W, H)
  if (!rows.length || !W) return H
  const [t0, t1] = span(rows), x0 = NAME(W), x1 = W - 4, X = (s: number) => x0 + Math.min(1, Math.max(0, (s - t0) / (t1 - t0))) * (x1 - x0)
  const small = W < 600
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
    { const x = X(row.hs + RACE); g.fillStyle = 'rgba(12, 15, 20, .55)'; for (let yy = y - 11; yy < y + 11; yy += 5) g.fillRect(x - 0.5, yy, 1, 3) }   // its 90 s, dashed
    // the passes: HS, then each lap's number(s) and time - past ones dark, ones to come faint
    const typical = [...row.laps].sort((a, b) => a - b)[Math.floor(row.laps.length / 2)] ?? 1
    g.font = `400 ${small ? 10 : 12}px ui-monospace, "SF Mono", Menlo, monospace`
    // a label goes right of its line (left of it at the right edge) and only where it does not run into the one before: the
    // line stays. A phone (under 600 px) gets the time alone, to 1/100 s, and no labels for the laps still to come
    // the lines first, then the labels over them on a paper ground, so the next pass's line does not cross the text
    let n = 0, free = -Infinity; const texts: [string, number, boolean][] = []
    const label = (x: number, s: string, done: boolean) => {
      if (small && !done) return
      const tw = g.measureText(s).width, lx = x + 4 + tw > x1 ? x - 4 - tw : x + 4
      if (lx < free) return
      texts.push([s, lx, done]); free = lx + tw + 6
    }
    { const x = X(row.hs), done = row.hs <= t; g.fillStyle = done ? '#a3161a' : 'rgba(163, 22, 26, .3)'; g.fillRect(x - 0.5, y - 10, 1, 20); label(x, 'HS', done) }
    row.ends.forEach((e, i) => {
      const lap = row.laps[i], m = Math.max(1, Math.round(lap / typical)), a = n + 1; n += m
      const x = X(e), done = e <= t; g.fillStyle = done ? '#a3161a' : 'rgba(163, 22, 26, .3)'; g.fillRect(x - 1, y - 10, 2, 20)
      label(x, small ? lap.toFixed(2) : `L${m > 1 ? `${a}-${n}` : n} ${lap.toFixed(3)}`, done)
    })
    const fs = small ? 10 : 12
    for (const [s, lx, done] of texts) {
      const tw = g.measureText(s).width; g.fillStyle = 'rgba(243, 244, 246, .9)'; g.fillRect(lx - 2, y - 4 - fs / 2 - 1, tw + 4, fs + 2)
      g.fillStyle = done ? '#0c0f14' : 'rgba(12, 15, 20, .35)'; g.fillText(s, lx, y - 4)
    }
  })
  if (t >= t0 && t <= t1) { g.fillStyle = '#a3161a'; g.fillRect(X(t) - 1, 0, 2, H) }   // the playhead
  return H
}

// the replay time under x on the canvas (CSS px), or null left of the axis: a click seeks there
export function timeAt(c: HTMLCanvasElement, rows: LapRow[], x: number): number | null {
  if (!rows.length) return null
  const [t0, t1] = span(rows), x0 = NAME(c.clientWidth), x1 = c.clientWidth - 4
  return x < x0 ? null : t0 + Math.min(1, (x - x0) / (x1 - x0)) * (t1 - t0)
}
