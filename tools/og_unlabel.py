"""Erase the 3D name tags (look.css .tag: a white box with the pilot's colour bar on its left) from a screenshot of the
3D page before og_image.py: near-white boxes 12-30 px tall and 40-160 px wide are filled from round them (OpenCV inpaint).
  uv run --with opencv-python-headless --with numpy python tools/og_unlabel.py <in.png> <out.png>"""
import sys
import cv2, numpy as np

im = cv2.imread(sys.argv[1])
white = cv2.inRange(im, (235, 235, 235), (255, 255, 255))
white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((5, 9), np.uint8))
n, lab, st, _ = cv2.connectedComponentsWithStats(white)
mask = np.zeros(im.shape[:2], np.uint8)
for i in range(1, n):
    x, y, w, h, a = st[i]
    if 12 <= h <= 30 and 40 <= w <= 160 and a > 0.6 * w * h:      # a tag: a short wide filled box
        print('tag', x, y, w, h)
        cv2.rectangle(mask, (x - 10, y - 3), (x + w + 3, y + h + 3), 255, -1)   # with the colour bar on its left
cv2.imwrite(sys.argv[2], cv2.inpaint(im, mask, 7, cv2.INPAINT_TELEA))
