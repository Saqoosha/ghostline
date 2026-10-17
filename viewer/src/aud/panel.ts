// The list of who flies, top left on the live and replay pages: a line per cell in its colour, the name with its flag and
// the state; a click follows that pilot (again: back to Orbit).
import { country, flagOf } from './flags'
import { SLOTS, type Mode, type PilotState, type Stage } from './stage'

const LABEL: Record<PilotState, string> = { flying: 'FLYING', lost: 'LOST', nopic: 'NO PICTURE' }
let key = ''
export function drawPilots(host: HTMLElement, stage: Stage, rows: { stream: string; slot?: string; name: string; state: PilotState }[], countries: Record<string, string>) {
  const k = rows.map(r => r.stream + r.name + r.state).join() + stage.mode
  if (k === key) return; key = k
  host.hidden = !rows.length
  host.replaceChildren(...rows.map((r, i) => {
    const b = document.createElement('button'), follow: Mode = `p${r.stream}`
    b.style.setProperty('--c', `var(--${r.slot ?? SLOTS[i] ?? 'tl'})`); b.className = (stage.mode === follow ? 'on ' : '') + (r.state === 'nopic' && !r.name ? 'off' : '')
    b.append(Object.assign(document.createElement('span'), { className: 'cell', textContent: r.stream }),
      Object.assign(document.createElement('span'), { textContent: r.name ? `${flagOf(country(r.name, countries))} ${r.name}`.trim() : '—' }),
      Object.assign(document.createElement('span'), { className: 'st ' + r.state, textContent: LABEL[r.state] }))
    b.onclick = () => stage.setMode(stage.mode === follow ? 'orbit' : follow)
    return b
  }))
}
export const redrawPilots = () => { key = '' }
