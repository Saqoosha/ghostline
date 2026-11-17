// The DVR column's width, dragged by its left edge (heat.html, compare.html; not on a phone, where the clips sit in a row):
// from MIN up to the width at which the clips shown fill the column's height (each a 22 px name band over a 4:3 picture,
// beside a 6 px colour bar), never leaving the 3D scene under SCENE. The width goes in a CSS variable the page's own width
// falls back from, so a phone's rule still wins, and this browser remembers it.
const MIN = 160, SCENE = 320, BAND = 22, BAR = 6
const grips = new WeakMap<HTMLElement, HTMLElement>()

export function dvrResize(pane: HTMLElement, cssVar: string, key: string) {
  let grip = grips.get(pane)
  if (!grip) {
    grip = document.createElement('div'); grip.className = 'dvr-grip'; grips.set(pane, grip)
    const g = grip, scene = document.getElementById('c')
    const max = () => Math.max(MIN, Math.min((pane.clientHeight / Math.max(1, pane.querySelectorAll('figure').length) - BAND) * 4 / 3 + BAR,
      pane.clientWidth + (scene?.clientWidth ?? Infinity) - SCENE))
    const set = (w: number) => { w = Math.round(Math.max(MIN, Math.min(max(), w))); document.documentElement.style.setProperty(cssVar, `${w}px`); return w }
    let saved = NaN; try { saved = Number(localStorage.getItem(key)) || NaN } catch { /* private window */ }
    const apply = () => { if (Number.isFinite(saved) && pane.clientHeight > 0) set(saved) }   // a hidden pane has no height to fit
    g.addEventListener('pointerdown', e => {
      if (e.button !== 0) return
      e.preventDefault(); g.setPointerCapture(e.pointerId); g.classList.add('on')
      const grab = innerWidth - e.clientX - pane.clientWidth          // where on the grip it was taken: no jump at the start
      const move = (m: PointerEvent) => { saved = set(innerWidth - m.clientX - grab) }
      const up = () => {
        g.removeEventListener('pointermove', move); g.removeEventListener('pointerup', up); g.removeEventListener('lostpointercapture', up)
        g.classList.remove('on'); try { localStorage.setItem(key, String(saved)) } catch { /* private window */ }
      }
      g.addEventListener('pointermove', move); g.addEventListener('pointerup', up); g.addEventListener('lostpointercapture', up)
    })
    addEventListener('resize', apply)
    new ResizeObserver(apply).observe(pane)                  // the clips shown (and so the largest width) change with the pane
  }
  pane.append(grip)                                          // again after the page refills the pane
}
