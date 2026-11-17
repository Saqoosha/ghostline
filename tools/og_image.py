"""A day's link preview (og.jpg, tools/publish-event.sh): a screenshot of the 3D page cropped to 1200x630 (1.91:1, the full
width; crop top in the screenshot's pixels, kept inside it), and a paper band with the ghostline mark, the title and the
date in site/index.html's look. Fonts are macOS's (Helvetica Neue, Menlo). Erase the name tags first (og_unlabel.py).
  uv run --with pillow python tools/og_image.py <screenshot> <title> <scene folder>/og.jpg [crop top px] [date]
  e.g. ... shot.png "UDGP 2026 R3" build/event-udgp-2026-r3/og.jpg 290 2026.11.15"""
import sys
from PIL import Image, ImageDraw, ImageFont

src, title, out = sys.argv[1:4]
top = int(sys.argv[4]) if len(sys.argv) > 4 else None
W, H = 1200, 630
im = Image.open(src).convert('RGB')
cw = im.width; ch = round(cw * H / W); left = 0
if ch > im.height: ch = im.height; cw = round(ch * W / H); left = (im.width - cw) // 2   # wider than 1.91:1: the middle
top = (im.height - ch) // 2 if top is None else max(0, min(top, im.height - ch))   # never past the picture
im = im.crop((left, top, left + cw, top + ch)).resize((W, H), Image.LANCZOS)

F = '/System/Library/Fonts/HelveticaNeue.ttc'
mark = ImageFont.truetype(F, 26, index=12)       # Thin, as the site's 200
head = ImageFont.truetype(F, 48, index=7)        # Light, as the site's 300
INK, PAPER = (12, 15, 20), (243, 244, 246)
DIM = tuple(round(i * 115 / 255 + p * (1 - 115 / 255)) for i, p in zip(INK, PAPER)) + (255,)   # opaque: a drawn alpha replaces the band's
GRAD = [(0xe8, 0xa8, 0x00), (0x22, 0xa8, 0x49), (0x2f, 0x7b, 0xf0), (0xf0, 0x70, 0x1e)]

def spaced(d, x, y, s, font, fill, sp):           # letter-spacing in em
    for c in s:
        d.text((x, y), c, font=font, fill=fill); x += d.textlength(c, font=font) + sp * font.size
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
while sum(d.textlength(c, font=head) + .06 * head.size for c in title) > W - 2 * pad - 180 and head.size > 24:
    head = ImageFont.truetype(F, head.size - 2, index=7)                         # a long title: smaller, room for the date
xe = spaced(d, pad - 3, y1 + 46 + (48 - head.size) // 2, title, head, INK + (255,), .06)
if len(sys.argv) > 5:                             # the date, beside the title on its baseline, as the site's .sub
    date = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 20)
    spaced(d, xe + 18, y1 + 46 + (48 - head.size) // 2 + head.getmetrics()[0] - date.getmetrics()[0], sys.argv[5], date, DIM, .14)
Image.alpha_composite(im.convert('RGBA'), ov).convert('RGB').save(out, quality=88)
print(out, W, H, 'crop top', top)
