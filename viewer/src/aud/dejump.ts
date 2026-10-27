// Answers that jump: after a lost stretch the tracker's relocalization can land on a wrong place 20-90 m away, and the
// relay lets it through once its HOLD has passed (JDL R7 10/25 h15: R5 177 jumps, F1 57). Per pilot, an answer farther
// from the last shown one than a drone can fly waits until the next ones agree with it (CONFIRM in a row: a wrong stretch runs 2-5 answers); a lone wrong
// answer is then dropped, and a true new place shows about 0.3 s late (the pages draw 0.8 s or more behind). On h14-h16 the jumps over 15 m fell 238 -> 89 for 1.7% of the answers.
const VMAX = 40, SLACK = 3, CAP = 0.5, CONFIRM = 8   // m/s, m, s (a long gap still needs agreement), answers

const reach = (a: number[], b: number[], dt: number) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) <= SLACK + VMAX * Math.min(Math.max(dt, 0), CAP)

export class Dejump<T> {
  private last: { t: number; p: number[] } | null = null
  private wait: { t: number; p: number[]; m: T }[] = []
  // one answer in (seconds, position) order per pilot; returns the answers to show now, in order
  feed(t: number, p: number[], m: T): T[] {
    if (this.last && t < this.last.t) return [m]                              // out of order (a reconnect's backlog): as is
    if (!this.last || reach(this.last.p, p, t - this.last.t)) { this.last = { t, p }; this.wait = []; return [m] }
    const tail = this.wait[this.wait.length - 1]
    if (tail && reach(tail.p, p, t - tail.t)) this.wait.push({ t, p, m }); else this.wait = [{ t, p, m }]
    if (this.wait.length < CONFIRM) return []
    const out = this.wait.map(w => w.m); this.last = { t, p }; this.wait = []
    return out
  }
}
