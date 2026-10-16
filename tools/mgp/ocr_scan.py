"""Read the race overlay of an 8up recording with macOS Vision: every STEP seconds, OCR the bar
on top of each of the 8 cells (place, pilot, LAP n, lap times) and the race clock cell.
usage: ocr_scan.py <video path or url> <out.json> [step=5]"""
import json, subprocess, sys
import numpy as np
from PIL import Image
from ocrmac import ocrmac

src, out = sys.argv[1], sys.argv[2]
STEP = float(sys.argv[3]) if len(sys.argv) > 3 else 5
dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src]))
ocr = lambda a: [t for t, _, _ in ocrmac.OCR(Image.fromarray(a), recognition_level="accurate").recognize()]
rows = []
t = 1.0
while t < dur - 1:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", src, "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    if len(raw) == 1920 * 1080 * 3:
        f = np.frombuffer(raw, np.uint8).reshape(1080, 1920, 3)
        cells = {}
        for s in range(8):
            r, c = divmod(s, 3)
            txt = ocr(np.ascontiguousarray(np.repeat(np.repeat(f[r * 360:r * 360 + 40, c * 640:c * 640 + 640], 2, 0), 2, 1)))
            if txt:
                cells[s] = txt
        clock = ocr(np.ascontiguousarray(f[720 + 100:720 + 260, 1280 + 100:1280 + 540]))
        rows.append({"t": t, "cells": cells, "clock": clock})
    t += STEP
json.dump({"src": src, "duration": dur, "rows": rows}, open(out, "w"))
print(out, len(rows), "samples")
