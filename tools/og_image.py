"""A day's link preview (og.jpg, tools/publish-event.sh): a screenshot of the 3D page cropped to 1200x630 (1.91:1: the full
width from crop top, in the screenshot's pixels and kept inside it; a screenshot wider than that loses both sides and the
top is ignored), and a paper band with the ghostline mark, the title and the date in site/index.html's look. Fonts are
macOS's (Helvetica Neue, Menlo; a title with Japanese in it is set in Hiragino Sans W3). Erase the name tags first
(og_unlabel.py).
  uv run --with pillow python tools/og_image.py <screenshot> <title> <scene folder>/og.jpg [crop top px] [date]
  e.g. ... shot.png "UDGP 2026 R3" build/event-udgp-2026-r3/og.jpg 290 2026.11.15"""
import sys
from PIL import Image, ImageDraw, ImageFont

src, title, out = sys.argv[1:4]
top = int(sys.argv[4]) if len(sys.argv) > 4 else None
when = sys.argv[5] if len(sys.argv) > 5 else ''
W, H = 1200, 630
im = Image.open(src).convert('RGB')
cw = im.width; ch = round(cw * H / W); left = 0
if ch > im.height: ch = im.height; cw = round(ch * W / H); left = (im.width - cw) // 2   # wider than 1.91:1: the middle
top = (im.height - ch) // 2 if top is None else max(0, min(top, im.height - ch))   # never past the picture
im = im.crop((left, top, left + cw, top + ch)).resize((W, H), Image.LANCZOS)

F = '/System/Library/Fonts/HelveticaNeue.ttc'
JP = '/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc'
mark = ImageFont.truetype(F, 26, index=12)       # Thin, as the site's 200
face = (lambda s: ImageFont.truetype(JP, s)) if any(ord(c) > 0x2FF and c not in '—–·' for c in title) else (lambda s: ImageFont.truetype(F, s, index=7))
head = face(48)                                  # Light, as the site's 300
date = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 20)
INK, PAPER = (12, 15, 20), (243, 244, 246)
DIM = tuple(round(i * 115 / 255 + p * (1 - 115 / 255)) for i, p in zip(INK, PAPER)) + (255,)   # opaque: a drawn alpha replaces the band's
GRAD = [(0xe8, 0xa8, 0x00), (0x22, 0xa8, 0x49), (0x2f, 0x7b, 0xf0), (0xf0, 0x70, 0x1e)]

def spaced(d, x, y, s, font, fill, sp):           # letter-spacing in em; fill None: measure only
    for c in s:
        if fill: d.text((x, y), c, font=font, fill=fill)
        x += d.textlength(c, font=font) + sp * font.size
    return x

band_h, pad = 150, 52
ov = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
d.rectangle((0, H - band_h, W, H), fill=PAPER + (238,))
y1 = H - band_h + 30
x = spaced(d, pad, y1, 'ghost', mark, DIM, .42)
gx0 = round(x + 4); gx1, gy = gx0 + 150, y1 + 16
for i in range(gx1 - gx0):                        # the four grid colours along the line
    t = i / (gx1 - gx0) * 3; k = min(2, int(t)); f = t - k
    c = tuple(round(GRAD[k][j] * (1 - f) + GRAD[k + 1][j] * f) for j in range(3))
    d.rectangle((gx0 + i, gy, gx0 + i, gy + 1), fill=c + (255,))
spaced(d, gx1 + 20, y1, 'line', mark, INK + (255,), .42)
room = W - 2 * pad - (spaced(d, 0, 0, when, date, None, .14) + 18 if when else 0)
while spaced(d, 0, 0, title, head, None, .06) > room and head.size > 24: head = face(head.size - 2)   # a long title: smaller
if spaced(d, 0, 0, title, head, None, .06) > room: sys.exit(f'the title does not fit even at {head.size} px: shorten it')
ty = y1 + 46 + (48 - head.size) // 2
xe = spaced(d, pad - 3, ty, title, head, INK + (255,), .06)
if when: spaced(d, xe + 18, ty + head.getmetrics()[0] - date.getmetrics()[0], when, date, DIM, .14)   # on the title's baseline
Image.alpha_composite(im.convert('RGBA'), ov).convert('RGB').save(out, quality=88)
print(out, W, H, 'crop top', top)
