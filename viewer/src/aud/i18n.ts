// Language for the audience pages: ?lang=ja|en (kept in localStorage), else the browser's language. Japanese stays the
// default for Japanese browsers. Guarded so bun tests can import modules that pull this in without a DOM.
const KEY = 'ghostline-lang'

function resolve(): 'ja' | 'en' {
  try {
    if (typeof location !== 'undefined') {
      const v = new URLSearchParams(location.search).get('lang')
      if (v === 'ja' || v === 'en') {
        try { localStorage.setItem(KEY, v) } catch { /* private window */ }
        return v
      }
    }
  } catch { /* */ }
  try {
    const s = localStorage.getItem(KEY)
    if (s === 'ja' || s === 'en') return s
  } catch { /* */ }
  try {
    if (typeof navigator !== 'undefined' && navigator.language?.startsWith('ja')) return 'ja'
  } catch { /* */ }
  return 'en'
}

export const LANG: 'ja' | 'en' = resolve()
export const L = (ja: string, en: string) => LANG === 'en' ? en : ja

// Static copy in the HTML: data-en / data-en-title / data-en-aria hold the English; Japanese stays in the markup.
export function applyStatic(root: ParentNode = document) {
  if (LANG === 'en') {
    for (const el of Array.from(root.querySelectorAll<HTMLElement>('[data-en]'))) el.textContent = el.dataset.en!
    for (const el of Array.from(root.querySelectorAll<HTMLElement>('[data-en-title]'))) el.title = el.dataset.enTitle!
    for (const el of Array.from(root.querySelectorAll<HTMLElement>('[data-en-aria]'))) el.setAttribute('aria-label', el.dataset.enAria!)
  }
  if (typeof document !== 'undefined') document.documentElement.lang = LANG
}

// Session headings, class names, heatName() results, page titles: Japanese tokens → English. Longest first so 準決勝 is
// not eaten by 決勝; 3・4 位決定 before the ・ separator.
const TOKENS: [string, string][] = [
  ['3・4 位決定', '3rd/4th place'],
  ['ファステストアタック', 'Fastest attack'],
  ['準々決勝', 'Quarterfinal'],
  ['準決勝', 'Semifinal'],
  ['決勝', 'Final'],
  ['予選', 'Qualifying'],
  ['練習', 'Practice'],
  ['1 回目', 'run 1'],
  ['2 回目', 'run 2'],
]
export function words(s: string | null): string {
  if (s == null) return ''
  if (LANG !== 'en') return s
  let out = s
  for (const [ja, en] of TOKENS) out = out.split(ja).join(en)
  return out.split('・').join(' · ')
}

export function langToggle(): HTMLElement {
  const a = document.createElement('a')
  a.className = 'btn'
  a.textContent = LANG === 'en' ? '日本語' : 'EN'
  const q = new URLSearchParams(location.search)
  q.set('lang', LANG === 'en' ? 'ja' : 'en')
  a.href = `${location.pathname}?${q}`
  return a
}
