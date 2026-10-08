"""rt_signal.py: does a cell of the receivers' 2x2 grid show a picture? Shared by rt_track.py (SIGNAL=1: a quiet cell sends the
tracker no frames) and rt_rec.py (a source is recorded while any of its cells has a picture).
On an EventVRX recording (2x2, analog, 707 s) the flat no-signal screens (grey, blue, black) have a pixel std of 0 and pictures
8 and up; snow is told apart by the correlation of neighbouring rows, 0.1-0.3 against 0.8-0.9 for a picture (0.5-0.7: a weak
signal with a faint picture). HDZero (FDF semifinal 2x2): flat grey or black, breakup 0.1-0.5."""
import cv2, numpy as np

def has_signal(cell):                               # BGR or BGRA crop of one cell
    g = cv2.resize(cell, (160, 90), interpolation=cv2.INTER_AREA)
    g = cv2.cvtColor(g, cv2.COLOR_BGRA2GRAY if g.shape[2] == 4 else cv2.COLOR_BGR2GRAY)[4:86, 4:156].astype(np.float32)
    if g.std() < 2: return False
    a0, a1 = g[:-1] - g[:-1].mean(), g[1:] - g[1:].mean()
    return float((a0 * a1).sum() / (np.sqrt((a0 ** 2).sum() * (a1 ** 2).sum()) + 1e-6)) >= 0.5

def cells_on(img):                                  # the four cells of a 2x2 grid frame, row by row (tl, tr, bl, br)
    h, w = img.shape[0] // 2, img.shape[1] // 2
    return [has_signal(img[y:y + h, x:x + w]) for y in (0, h) for x in (0, w)]
