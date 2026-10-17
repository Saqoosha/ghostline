// a flag beside each name (from livepage.ts): the heat manager gives names only, so the country is guessed from the name -
// kana or kanji: JP; a Korean family name among the romanized words: KR - unless the day's countries.json ({name: ISO-2}) says
// (not the ones that are also romanized Japanese or English: yu, ko, go, jin, han, jo, an, min, son, ha, na, ji, im)
const KR = new Set('kim lee yi park choi jung jeong kang cho yoon yun jang lim oh seo shin kwon hwang ahn song yoo hong jeon moon yang bae baek heo nam noh kwak sung cha joo woo ryu uhm chae won byun bang yeo pyo myung'.split(' '))
export const country = (name: string, over: Record<string, string> = {}) => (Object.hasOwn(over, name) ? over[name] : null)
  ?? (/[぀-ヿ一-鿿]/.test(name) ? 'JP' : name.toLowerCase().split(/[\s-]+/).some(w => KR.has(w)) ? 'KR' : '')
export const flagOf = (cc: string) => /^[A-Z]{2}$/.test(cc) ? String.fromCodePoint(...[...cc].map(c => 0x1f1e6 + c.charCodeAt(0) - 65)) : ''
