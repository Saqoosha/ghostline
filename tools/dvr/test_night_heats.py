"""tests for night_heats.py (python -m unittest test_night_heats -v, from tools/dvr)"""
import unittest
from night_heats import clip_window, grid, heats_of

REC = dict(start=1000.0, fps=60.0, frames=6000)       # 100 s from wall 1000


class ClipWindowTest(unittest.TestCase):
    def test_inside(self): self.assertEqual(clip_window(REC, 1010, 1020), (10, 20))
    def test_clipped_at_start(self): self.assertEqual(clip_window(REC, 990, 1005), (0, 5))
    def test_clipped_at_end(self): self.assertEqual(clip_window(REC, 1090, 1110), (90, 100))
    def test_outside(self): self.assertIsNone(clip_window(REC, 1200, 1210))


class GridTest(unittest.TestCase):
    def test_line_on_30fps(self):                      # answers at 60 Hz on x = t: frame k of a 30 fps grid from t0 sits at x = t0 + k / 30
        ans = [(100 + k / 60, [k / 60, 1.0, 0.0]) for k in range(240)]
        g = grid(ans, 100.0, 104.0, 30)
        self.assertEqual(len(g), 121); self.assertAlmostEqual(g[45][0], 1.5, places=3)

    def test_gap_left_empty(self):
        ans = [(100 + k / 60, [0.0, 1.0, 0.0]) for k in range(60)] + [(102 + k / 60, [0.0, 1.0, 0.0]) for k in range(60)]
        g = grid(ans, 100.0, 103.0, 30)
        self.assertIsNone(g[45]); self.assertIsNotNone(g[15]); self.assertIsNotNone(g[75])


class HeatsOfTest(unittest.TestCase):
    def test_last_names_and_end(self):
        rows = [dict(k="h", t=1000, at=1000, id="a", heat=None, cls=None, cells={}, order=["R2"]),
                dict(k="h", t=1000, at=5000, id="a", heat="7", cls="X", cells={"R2": "A"}, order=["R2"]),
                dict(k="e", t=9000, id="a"), dict(k="h", t=20000, at=20000, id="b", heat=None, cls=None, cells={}, order=["R2"])]
        hs = heats_of(rows)
        self.assertEqual([(h["id"], h["start"], h["end"], h["heat"]) for h in hs], [("a", 1000, 9000, "7")])   # b has not ended


if __name__ == "__main__": unittest.main()
