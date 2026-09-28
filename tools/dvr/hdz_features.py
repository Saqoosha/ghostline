"""Per-block features for HDZero dropout blocks (see hdz_blocks.py for the grid).

A dropout corrupts the block's DCT coefficients, so its kinds differ - a brightness shift (luma DC), a blue / pink /
purple cast (chroma DC), a square that looks like a lower-quality JPEG of the same place (AC lost), checker junk (AC
garbage) - but every kind shifts the whole block against its neighbours, so the step across its border runs the same
way on all four sides. Real edges almost never line up with the 10.67 px grid on four sides at once. The features:
per channel (L, a, b) the signed step across each side (inner edge row minus outer), their mean, spread and weakest
side; the texture inside against the neighbours'; the colour offset from the neighbourhood; and the difference from
the previous and next frames warped onto this one (junk is new every frame, a real object moves along)."""
import cv2, numpy as np

W, H = 960, 720
def grid(block=32 / 3):
    bx = np.floor(np.arange(W) / block).astype(int); by = np.floor(np.arange(H) / block).astype(int)
    xe = np.r_[np.flatnonzero(np.diff(bx)) + 1]; ye = np.r_[np.flatnonzero(np.diff(by)) + 1]     # first pixel of each block after the first
    return bx, by, xe, ye
BX, BY, XE, YE = grid(); NBX, NBY = BX[-1] + 1, BY[-1] + 1; NB = NBX * NBY
BID = (BY[:, None] * NBX + BX[None, :]).ravel()
def bmean(img):
    n = np.bincount(BID, minlength=NB); return (np.stack([np.bincount(BID, img[..., k].ravel(), NB) for k in range(img.shape[2])], 1) / n[:, None]).reshape(NBY, NBX, -1)
def sides(lab):
    """signed step inner-minus-outer on each side, per channel: [4, NBY, NBX, 3] (NaN at the frame border)"""
    out = np.full((4, NBY, NBX, 3), np.nan)
    colin = np.stack([np.bincount(BY, lab[:, x, k], NBY) / np.bincount(BY, minlength=NBY) for x in XE for k in range(3)], 1).reshape(NBY, len(XE), 3)
    colout = np.stack([np.bincount(BY, lab[:, x - 1, k], NBY) / np.bincount(BY, minlength=NBY) for x in XE for k in range(3)], 1).reshape(NBY, len(XE), 3)
    rowin = np.stack([np.bincount(BX, lab[y, :, k], NBX) / np.bincount(BX, minlength=NBX) for y in YE for k in range(3)], 1).reshape(NBX, len(YE), 3).transpose(1, 0, 2)
    rowout = np.stack([np.bincount(BX, lab[y - 1, :, k], NBX) / np.bincount(BX, minlength=NBX) for y in YE for k in range(3)], 1).reshape(NBX, len(YE), 3).transpose(1, 0, 2)
    out[0, :, 1:] = colin - colout                       # left side of blocks 1..: inside col x minus col x-1
    out[1, :, :-1] = colout - colin                      # right side of blocks ..-1: inside col x-1 minus col x
    out[2, 1:, :] = rowin - rowout; out[3, :-1, :] = rowout - rowin
    return out
def texture(lab):
    L = lab[..., 0]; dx = np.abs(np.diff(L, axis=1)); dy = np.abs(np.diff(L, axis=0))
    ix = np.ones(W - 1, bool); ix[XE - 1] = False; iy = np.ones(H - 1, bool); iy[YE - 1] = False
    tx = np.bincount((BY[:, None] * NBX + BX[None, :-1])[:, ix].ravel(), dx[:, ix].ravel(), NB) / np.maximum(np.bincount((BY[:, None] * NBX + BX[None, :-1])[:, ix].ravel(), minlength=NB), 1)
    ty = np.bincount((BY[:-1, None] * NBX + BX[None, :])[iy].ravel(), dy[iy].ravel(), NB) / np.maximum(np.bincount((BY[:-1, None] * NBX + BX[None, :])[iy].ravel(), minlength=NB), 1)
    return ((tx + ty) / 2).reshape(NBY, NBX)
def neigh(a, fn):
    p = np.pad(a, [(1, 1), (1, 1)] + [(0, 0)] * (a.ndim - 2), mode="edge")
    stack = np.stack([p[1 + dy:1 + dy + a.shape[0], 1 + dx:1 + dx + a.shape[1]] for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx])
    return fn(stack, 0)
def warp_to(cur, nb):
    g0 = cv2.cvtColor(cv2.resize(cur, (480, 360)), cv2.COLOR_BGR2GRAY); g1 = cv2.cvtColor(cv2.resize(nb, (480, 360)), cv2.COLOR_BGR2GRAY)
    fl = cv2.resize(cv2.calcOpticalFlowFarneback(g0, g1, None, 0.5, 4, 31, 5, 7, 1.5, 0), (W, H)) * 2; mx, my = np.meshgrid(np.arange(W), np.arange(H))
    return cv2.remap(nb, (mx + fl[..., 0]).astype(np.float32), (my + fl[..., 1]).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
def features(cur, prev, nxt):
    lab = cv2.cvtColor(cur, cv2.COLOR_BGR2LAB).astype(np.float64); m = bmean(lab); s = sides(lab)
    f = []
    for k in range(3):
        sk = s[..., k]; mean = np.nanmean(sk, 0); f += [mean, np.nanstd(sk, 0), np.nanmin(np.abs(sk), 0),
              np.nansum(np.sign(sk) == np.sign(mean)[None], 0)]                           # how many sides agree in sign
    t = texture(lab); tn = neigh(t, np.median); f += [np.log1p(t), np.log1p(t) - np.log1p(tn)]
    mn = neigh(m, np.median); f += [np.linalg.norm(m - mn, axis=-1), np.linalg.norm((m - mn)[..., 1:], axis=-1), np.linalg.norm(m[..., 1:] - 128, axis=-1)]
    dt = [np.linalg.norm(m - bmean(cv2.cvtColor(warp_to(cur, o), cv2.COLOR_BGR2LAB).astype(np.float64)), axis=-1) for o in (prev, nxt)]
    f += [np.minimum(*dt), np.maximum(*dt)]
    return np.nan_to_num(np.stack(f, -1))                                                   # [NBY, NBX, F]
